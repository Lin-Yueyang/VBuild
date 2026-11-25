"""A compact movable representation picker with English style names."""

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QDialog, QHBoxLayout, QListWidget, QMessageBox, QPushButton, QRadioButton, QVBoxLayout
from .styles import GENERAL, TITLES

from .look import KINDS
from .overlay import Overlay


class StyleManager(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle("管理样式")
        self.resize(340, 285)
        layout = QVBoxLayout(self)
        self.items = QListWidget()
        layout.addWidget(self.items)
        row = QHBoxLayout()
        load = QPushButton("导入样式")
        self.remove = QPushButton("删除样式")
        close = QPushButton("关闭")
        for button in (load, self.remove, close):
            row.addWidget(button)
        layout.addLayout(row)
        self.items.currentTextChanged.connect(lambda name: self.remove.setEnabled(bool(name) and name not in TITLES))
        load.clicked.connect(self.import_file)
        self.remove.clicked.connect(self.remove_file)
        close.clicked.connect(self.accept)
        self.refresh()

    def refresh(self, selected=None):
        self.items.clear()
        self.items.addItems([name for name in self.window.style_library if name not in GENERAL])
        self.items.setCurrentRow(0)
        if selected:
            for index in range(self.items.count()):
                if self.items.item(index).text() == selected:
                    self.items.setCurrentRow(index)
                    break

    def import_file(self):
        self.refresh(self.window.import_view_style())

    def remove_file(self):
        name = self.items.currentItem().text()
        if QMessageBox.question(self, "删除样式", f"从样式库删除「{name}」？") == QMessageBox.StandardButton.Yes:
            try:
                self.window.remove_saved_style(name)
            except OSError as error:
                QMessageBox.critical(self, "样式无法删除", str(error))
                return
            self.refresh()


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
