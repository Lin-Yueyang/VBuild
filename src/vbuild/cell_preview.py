"""Two independent cell previews with cell metrics and preserved cameras."""

from ase import Atoms
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QHBoxLayout, QPushButton, QSplitter, QVBoxLayout, QWidget

from .scene import Scene


def title(name, structure):
    formula = Atoms(symbols=structure.symbols).get_chemical_formula(mode='metal', empirical=True)
    return name + '（' + formula.translate(str.maketrans('0123456789', '₀₁₂₃₄₅₆₇₈₉')) + '）'


class CellView(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.scene = None
        self.layout_box = QVBoxLayout(self)
        self.layout_box.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        self.heading = QLabel()
        header.addWidget(self.heading, 1)
        self.fit = QPushButton('适应')
        self.fit.setFixedWidth(46)
        self.fit.setEnabled(False)
        self.fit.clicked.connect(lambda: self.scene.plotter.reset_camera())
        header.addWidget(self.fit)
        self.layout_box.addLayout(header)
        self.placeholder = QLabel('选择参考结构')
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.layout_box.addWidget(self.placeholder, 1)
        self.setMinimumWidth(210)

    def display(self, name, structure, *, reset=False):
        self.heading.setText(title(name, structure))
        if self.scene is None:
            self.scene = Scene(self)
            self.scene.previewing = True
            self.scene.show_cell_info = True
            self.scene.setMinimumSize(210, 250)
            self.layout_box.addWidget(self.scene, 1)
            self.scene.show()
            reset = True
        camera = self.scene.camera_snapshot()
        self.placeholder.hide()
        self.scene.show()
        self.layout_box.activate()
        self.scene.set_structure(structure, reset=reset)
        if not reset:
            self.scene.restore_camera(camera, fit_clipping=True)
        self.fit.setEnabled(True)

    def clear(self):
        self.heading.setText('参考')
        self.placeholder.show()
        if self.scene is not None:
            self.scene.hide()
        self.fit.setEnabled(False)

    def close_view(self):
        if self.scene is not None:
            self.scene.plotter.close()


class CellPreview(QSplitter):
    def __init__(self, parent):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setChildrenCollapsible(False)
        self.setHandleWidth(7)
        self.current, self.reference = CellView(self), CellView(self)
        self.addWidget(self.current)
        self.addWidget(self.reference)
        self.setSizes([1, 1])

    def close_views(self):
        self.current.close_view()
        self.reference.close_view()
