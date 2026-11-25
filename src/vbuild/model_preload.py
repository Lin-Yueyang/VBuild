"""One background queue for the models explicitly selected at startup."""

from threading import Lock

from ase.calculators.calculator import Calculator
from PySide6.QtCore import QObject, Signal

from .mlip import MODELS
from .relax_worker import LoadModel


class SharedCalculator(Calculator):
    """ASE calculators cache the last structure; serialize each property request."""

    def __init__(self, model):
        super().__init__()
        self.model = model
        self.implemented_properties = model.implemented_properties
        self.lock = Lock()

    def get_property(self, name, atoms=None, allow_calculation=True):
        with self.lock:
            return self.model.get_property(name, atoms, allow_calculation)


class ModelPreload(QObject):
    changed = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.queue = []
        self.active = self.worker = None
        self.loaded = {}
        self.errors = {}

    def start(self, models, paths, device):
        self.queue = [(name, paths[name], device) for name in models if name in MODELS]
        self.advance()

    def advance(self):
        while self.queue:
            key = self.queue.pop(0)
            model, path, device = key
            if not path.is_file():
                self.errors[key] = "未找到模型文件"
                continue
            self.active = key
            self.worker = LoadModel(path, device, self, model=model)
            self.worker.ready.connect(self.receive)
            self.worker.failed.connect(self.failed)
            self.worker.finished.connect(self.finished)
            self.worker.start()
            break
        self.changed.emit()

    def receive(self, calculator):
        self.loaded[self.active] = SharedCalculator(calculator)
        self.changed.emit()

    def failed(self, error):
        self.errors[self.active] = error
        self.changed.emit()

    def finished(self):
        self.worker.deleteLater()
        self.worker = self.active = None
        self.advance()
