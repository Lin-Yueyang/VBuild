"""Per-element color overrides for the modeling view."""

from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import QColorDialog, QHBoxLayout, QPushButton, QWidget

from .atoms import ElementButton


class ElementColors(QWidget):
    def __init__(self, overrides, elements, initial):
        super().__init__()
        self.values = dict(overrides)
        self.elements = elements
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.element = ElementButton()
        self.element.set_symbol(initial)
        self.color = QPushButton()
        self.reset = QPushButton("恢复风格配色")
        self.element.symbol_changed.connect(self.refresh)
        self.color.clicked.connect(self.choose)
        self.reset.clicked.connect(self.reset_current)
        for button in (self.element, self.color, self.reset):
            layout.addWidget(button)
        layout.addStretch()
        self.refresh()

    def refresh(self):
        symbol = self.element.symbol
        color = self.values.get(symbol, self.elements[symbol]["color"])
        swatch = QPixmap(16, 16)
        swatch.fill(QColor(color))
        self.color.setIcon(QIcon(swatch))
        self.color.setText(color.upper())
        self.reset.setEnabled(symbol in self.values)

    def choose(self):
        color = QColorDialog.getColor(QColor(self.color.text()), self, self.element.symbol + " 原子颜色")
        if color.isValid():
            self.values[self.element.symbol] = color.name().upper()
            self.refresh()

    def reset_current(self):
        self.values.pop(self.element.symbol)
        self.refresh()
