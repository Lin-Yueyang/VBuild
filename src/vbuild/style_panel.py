"""A compact movable representation picker with English style names."""

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QRadioButton, QVBoxLayout

from .look import KINDS
from .overlay import Overlay


class StylePanel(Overlay):
    chosen = Signal(str)
    closed = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("style_panel")
        self.setStyleSheet("#style_panel { background: #f8f9fa; border: 1px solid #aeb6c2; }")
        self.setFixedWidth(225)
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.grip.setText("模型风格 · Space 切换")
        row.addWidget(self.grip, 1)
        close = QPushButton("×")
        close.setFixedSize(22, 22)
        close.clicked.connect(self.closed)
        row.addWidget(close)
        layout.addLayout(row)
        self.buttons = {}
        for key, label in KINDS:
            button = QRadioButton(label)
            button.setIcon(QIcon(str(Path(__file__).with_name("icons") / ("style_" + key + ".svg"))))
            button.clicked.connect(lambda _checked, kind=key: self.chosen.emit(kind))
            self.buttons[key] = button
            layout.addWidget(button)
        self.hide()

    def populate(self, kind):
        self.buttons[kind].setChecked(True)
