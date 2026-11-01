"""Load local MLIPs and run ASE in background threads; never download a model."""

import pickle
import json
import subprocess
import sys
from threading import Lock

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from .relax import optimize
from .md import equilibrate
from .launch import MACE, NEP, missing_message, versions
from .mlip import MODELS, model_paths
from .runtime import engine_python, NO_WINDOW, process_env
from .devices import gpu_device, has_gpu_backend, check_device


class Hardware(QThread):
    ready = Signal(str, str)

    def __init__(self, parent, model="nep89"):
        super().__init__(parent)
        self.model = model

    def run(self):
        gpu, cpu = "Fail", "Fail"
        # Hardware detection is a system-query boundary; report Fail without substitution.
        try:
            external = engine_python(MODELS[self.model].engine, gpu_device())
            if external:
                from pathlib import Path
                result = subprocess.run([external, '-B', str(Path(__file__).with_name('engine.py')), 'hardware', MODELS[self.model].engine],
                    capture_output=True, text=True, encoding='utf-8', timeout=60, env=process_env(external), creationflags=NO_WINDOW)
                if result.returncode == 0:
                    gpu = json.loads(result.stdout)['gpu']
            elif not has_gpu_backend(MODELS[self.model].engine):
                gpu = "不支持"
            elif MODELS[self.model].engine == "nep":
                from .nep import prepare_backend
                prepare_backend()
                from nep_adapters import backend_status
                status = backend_status("cuda")
                if status.available:
                    gpu = status.devices[0].name
            else:
                from .engine import hardware
                gpu = hardware('mace')
        except (ImportError, RuntimeError, OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
            pass
        commands = {"win32": ["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_Processor).Name"],
                    "darwin": ["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"]}
        if sys.platform in commands:
            try:
                result = subprocess.run(commands[sys.platform], capture_output=True, text=True, timeout=20,
                                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
                if result.returncode == 0 and result.stdout.strip():
                    cpu = result.stdout.strip()
            except (OSError, subprocess.TimeoutExpired):
                pass
        self.ready.emit(gpu, cpu)


class LoadModel(QThread):
    ready = Signal(object)
    failed = Signal(str)

    def __init__(self, path, device, parent, *, model="mp-0", head=None):
        super().__init__(parent)
        self.path, self.device = path, device
        self.model = MODELS[model]
        self.head = self.model.head if head is None else head

    def run(self):
        nep = self.model.engine == "nep"
        external = engine_python(self.model.engine, self.device)
        if external:
            from .engine import RemoteCalculator
            try:
                self.ready.emit(RemoteCalculator(external, next(key for key, item in MODELS.items() if item is self.model), self.path, self.device))
            except (OSError, RuntimeError, ValueError) as error:
                self.failed.emit(str(error))
            return
        _found, missing = versions(NEP if nep else MACE)
        if missing:
            self.failed.emit(missing_message(missing, "nep" if nep else "relax"))
            return
        # External model/deserialization boundary; no CPU or model fallback.
        try:
            check_device(self.model.engine, self.device)
            if nep:
                from .nep import calculator as nep_calculator
                calculator = nep_calculator(self.path, self.device)
            else:
                import torch
                torch.set_num_threads(4)
                from mace.calculators import MACECalculator
                parameters = {} if self.head is None else {"head": self.head}
                calculator = MACECalculator(model_paths=str(self.path), device=self.device,
                                            default_dtype="float32", **parameters)
                if self.head is not None and self.head not in calculator.available_heads:
                    raise ValueError(f"模型不包含指定 head：{self.head}")
        except (ImportError, OSError, RuntimeError, ValueError, EOFError, pickle.UnpicklingError) as error:
            self.failed.emit(str(error))
            return
        self.ready.emit(calculator)


class RunOptimization(QThread):
    done = Signal(str)

    def __init__(self, structure, calculator, state, parent):
        super().__init__(parent)
        self.structure, self.calculator = structure, calculator
        self.kind = state.kind
        self.fmax, self.max_steps = state.fmax, state.max_steps
        self.temperature, self.timestep, self.friction = state.temperature, state.timestep, state.friction
        self.walls = state.walls
        self.pending, self.lock = [], Lock()

    def record(self, step):
        with self.lock:
            self.pending.append(step)

    def take_frames(self):
        with self.lock:
            frames, self.pending = self.pending, []
        return frames

    def run(self):
        # Inference/optimizer boundary: retain completed frames and show the actual error.
        try:
            if self.kind == "md":
                status = equilibrate(self.structure, self.calculator, self.temperature, self.max_steps,
                                     self.timestep, self.friction, self.record, self.isInterruptionRequested,
                                     walls=self.walls)
            else:
                status = optimize(self.structure, self.calculator, self.fmax, self.max_steps,
                                  self.record, self.isInterruptionRequested)
        except (ValueError, RuntimeError, OSError, TypeError, KeyError) as error:
            status = ("预平衡失败：" if self.kind == "md" else "优化失败：") + str(error)
        self.done.emit(status)


class RelaxController(QObject):
    changed = Signal()
    frames_ready = Signal(object, object)

    def __init__(self, path, parent, *, model="nep89", paths=None):
        super().__init__(parent)
        self.path = path
        self.model = model
        self.paths = model_paths() if paths is None else dict(paths)
        self.paths[model] = path
        self.device = "cpu" if sys.platform == "darwin" else "cuda"
        self.gpu, self.cpu = "检测中", "检测中"
        self.calculator = None
        self.message = "模型尚未加载"
        self.hardware = None
        self.loader = None
        self.task = None
        self.owner = None
        self.updates = QTimer(self)
        self.updates.setInterval(50)
        self.updates.timeout.connect(self.drain_frames)

    @property
    def busy(self):
        return self.hardware is not None or self.loader is not None or self.task is not None

    def detect(self):
        if self.gpu != "检测中" or self.hardware is not None:
            return
        self.hardware = Hardware(self, self.model)
        self.hardware.ready.connect(self.hardware_ready)
        self.hardware.finished.connect(self.hardware_finished)
        self.hardware.start()

    def hardware_ready(self, gpu, cpu):
        self.gpu, self.cpu = gpu, cpu
        self.changed.emit()

    def hardware_finished(self):
        self.hardware.deleteLater()
        self.hardware = None
        self.changed.emit()

    def select(self, path, device, model=None):
        model = self.model if model is None else model
        if (path, device, model) != (self.path, self.device, self.model):
            engine_changed = MODELS[model].engine != MODELS[self.model].engine
            self.path, self.device = path, device
            self.model = model
            self.paths[model] = path
            self.calculator = None
            self.message = "模型尚未加载"
            if engine_changed:
                self.gpu, self.cpu = "检测中", "检测中"
                self.detect()
            self.changed.emit()

    def load(self):
        self.message = "正在加载模型"
        self.loader = LoadModel(self.path, self.device, self, model=self.model)
        self.loader.ready.connect(self.loaded)
        self.loader.failed.connect(self.failed)
        self.loader.finished.connect(self.load_finished)
        self.loader.start()
        self.changed.emit()

    def loaded(self, calculator):
        self.calculator, self.message = calculator, "模型已加载"
        self.changed.emit()

    def failed(self, error):
        self.message = "加载失败：" + error
        self.changed.emit()

    def load_finished(self):
        self.loader.deleteLater()
        self.loader = None
        self.changed.emit()

    def start(self, session, structure):
        state = session.relaxation
        state.reset(structure.positions)
        state.running, state.status = True, "正在预平衡" if state.kind == "md" else "正在优化"
        self.owner = session
        self.task = RunOptimization(structure, self.calculator, state, self)
        self.task.done.connect(lambda status: self.completed(session, status))
        self.task.finished.connect(self.run_finished)
        self.task.start()
        self.updates.start()
        self.changed.emit()

    def drain_frames(self):
        frames = self.task.take_frames()
        if frames:
            self.frames_ready.emit(self.owner, frames)

    def completed(self, session, status):
        self.drain_frames()
        session.relaxation.status = status

    def run_finished(self):
        self.updates.stop()
        self.drain_frames()
        self.owner.relaxation.running = False
        self.task.deleteLater()
        self.task, self.owner = None, None
        self.changed.emit()

    def stop(self):
        if self.task is not None:
            self.owner.relaxation.status = "正在停止，等待当前步完成"
            self.task.requestInterruption()
            self.changed.emit()
