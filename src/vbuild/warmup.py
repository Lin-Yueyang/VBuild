"""Preload construction and ASE tools after the first window appears."""

import atexit
from importlib import import_module
import sys
from time import perf_counter

from PySide6.QtCore import QCoreApplication, QThread, Signal


MODULES = (("成键邻居", "ase.neighborlist"), ("几何计算", "scipy.spatial"),
           ("分子与水构建", "vbuild.molecules"), ("切面扩胞", "vbuild.build_dialog"),
           ("结构构建", "vbuild.create_dialog"), ("碳结构", "ase.build"),
           ("ASE 优化器", "ase.optimize.fire"), ("ASE 动力学", "ase.md.langevin"),
           ("LAMMPS 结构", "ase.io.lammpsdata"), ("LAMMPS 轨迹", "ase.io.lammpsrun"),
           ("CIF 格式", "ase.io.cif"), ("VASP 格式", "ase.io.vasp"),
           ("二维切片绘图", "matplotlib.backends.backend_qtagg"),
           ("Bader 电荷", "vbuild.bader_panel"), ("磁矩", "vbuild.moment_panel"), ("NEB 查看", "vbuild.neb_ui"))


class ImportTask(QThread):
    progress = Signal(str)
    loaded = Signal(str)
    completed = Signal()
    failed = Signal(object, object)

    def __init__(self, modules, parent):
        super().__init__(parent)
        self.modules, self.ready, self.current, self.error = modules, set(), "", None
        self.timings = []

    def run(self):
        # This is the optional-library initialization boundary; GUI objects stay on the GUI thread.
        try:
            for label, name in self.modules:
                if self.isInterruptionRequested():
                    return
                self.current = label
                self.progress.emit(label)
                started = perf_counter()
                import_module(name)
                duration = perf_counter() - started
                self.timings.append((name, duration))
                print(f"Startup: import {name} {duration:.3f}s", flush=True)
                self.ready.add(name)
                self.loaded.emit(name)
        except Exception as error:
            self.error = error
            self.failed.emit(error, sys.exc_info()[2])
            return
        self.current = ""
        self.completed.emit()

    def finish(self):
        self.requestInterruption()
        self.wait()


_task = None


def start():
    global _task
    if _task is None:
        app = QCoreApplication.instance()
        _task = ImportTask(MODULES, app)
        app.aboutToQuit.connect(_task.finish)
        atexit.register(_task.finish)
        _task.start()
    return _task
