"""Single-frame MLIP forces; latest snapshot wins and inference stays off the GUI."""

from copy import deepcopy
from hashlib import sha256

import numpy as np
from ase import Atoms
from PySide6.QtCore import QThread, QTimer, Signal

from .relax_worker import RelaxController


def signature(structure):
    value = sha256(" ".join(structure.symbols).encode())
    value.update(structure.positions.tobytes())
    value.update(b"none" if structure.cell is None else structure.cell.tobytes())
    value.update(bytes(structure.pbc))
    return value.digest()


def calculate(structure, calculator):
    atoms = Atoms(symbols=structure.symbols, positions=structure.positions,
                  cell=structure.cell, pbc=structure.pbc)
    atoms.calc = calculator
    forces = np.asarray(atoms.get_forces(apply_constraint=False))
    if forces.shape != (len(structure.symbols), 3) or not np.isfinite(forces).all():
        raise ValueError("模型返回无效的力，已关闭受力显示。")
    return forces.copy()


class ForceWorker(QThread):
    ready = Signal(object, object)
    failed = Signal(object, str)

    def __init__(self, structure, key, calculator, parent):
        super().__init__(parent)
        self.structure, self.key, self.calculator = structure, key, calculator

    def run(self):
        # External inference boundary: report the actual failure, no model/device substitution.
        try:
            self.ready.emit(self.key, calculate(self.structure, self.calculator))
        except (ImportError, OSError, RuntimeError, ValueError, KeyError) as error:
            self.failed.emit(self.key, str(error))


class ForceController(RelaxController):
    def __init__(self, path, parent, *, model="nep89", paths=None):
        super().__init__(path, parent, model=model, paths=paths)
        self.enabled = False
        self.values, self.result_key, self.target = None, None, None
        self.threshold, self.scale = 1.0, 1.0
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(120)
        self.timer.timeout.connect(self.compute)

    def select(self, path, device, model=None):
        name = self.model if model is None else model
        if (path, device, name) != (self.path, self.device, self.model):
            self.enabled = False
            self.values, self.result_key = None, None
            self.timer.stop()
        super().select(path, device, model)

    def loaded(self, calculator):
        self.values, self.result_key = None, None
        super().loaded(calculator)

    def set_charge_spin(self, charge, multiplicity, *, automatic=False):
        self.enabled = False
        self.values, self.result_key = None, None
        self.timer.stop()
        super().set_charge_spin(charge, multiplicity, automatic=automatic)

    def enable(self, enabled):
        self.enabled = enabled
        if not enabled:
            self.timer.stop()
        self.changed.emit()

    def request(self, structure):
        if not self.enabled or self.calculator is None or structure is None or not structure.symbols:
            self.target = None
            self.timer.stop()
            return None
        key = signature(structure)
        if self.target is None or key != self.target[0]:
            self.target = (key, deepcopy(structure))
        if key == self.result_key:
            return self.values
        if not self.busy and not self.timer.isActive():
            self.timer.start()
        return None

    def compute(self):
        if not self.enabled or self.target is None or self.busy:
            return
        key, structure = self.target
        self.task = ForceWorker(structure, key, self.calculator, self)
        self.task.ready.connect(self.receive)
        self.task.failed.connect(self.inference_failed)
        self.task.finished.connect(self.force_finished)
        self.task.start()
        self.changed.emit()

    def receive(self, key, values):
        if self.enabled and self.target is not None and key == self.target[0]:
            self.values, self.result_key = values, key
            self.message = "受力已更新"
            self.changed.emit()

    def inference_failed(self, key, error):
        if self.target is not None and key == self.target[0]:
            self.enabled = False
            self.values, self.result_key = None, None
            self.message = "计算失败：" + error
            self.changed.emit()

    def force_finished(self):
        self.task.deleteLater()
        self.task = None
        self.changed.emit()

    def stop(self):
        self.enabled = False
        self.target = None
        self.timer.stop()
        if self.task is not None:
            self.task.requestInterruption()
