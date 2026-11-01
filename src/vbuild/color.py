"""HSV wheel with brightness and hexadecimal input, shared by material editors."""

import math

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QConicalGradient, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QSlider, QVBoxLayout, QWidget


class Wheel(QWidget):
    changed = Signal(QColor)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.color = QColor("#FFFFFF")
        self.setMinimumSize(180, 180)
        self.setMaximumHeight(210)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QPointF(self.width() / 2, self.height() / 2)
        radius = min(self.width(), self.height()) / 2 - 8
        hue = QConicalGradient(center, 0)
        for index in range(7):
            hue.setColorAt(index / 6, QColor.fromHsvF((index % 6) / 6, 1, 1))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(hue)
        painter.drawEllipse(center, radius, radius)
        saturation = QRadialGradient(center, radius)
        saturation.setColorAt(0, QColor("white"))
        saturation.setColorAt(1, QColor(255, 255, 255, 0))
        painter.setBrush(saturation)
        painter.drawEllipse(center, radius, radius)
        painter.setBrush(QColor(0, 0, 0, round(255 * (1 - self.color.valueF()))))
        painter.drawEllipse(center, radius, radius)
        angle = max(0, self.color.hueF()) * 2 * math.pi
        distance = self.color.saturationF() * radius
        marker = center + QPointF(math.cos(angle) * distance, -math.sin(angle) * distance)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("black"), 3))
        painter.drawEllipse(marker, 5, 5)
        painter.setPen(QPen(QColor("white"), 1))
        painter.drawEllipse(marker, 5, 5)

    def choose(self, point):
        x, y = point.x() - self.width() / 2, self.height() / 2 - point.y()
        radius = min(self.width(), self.height()) / 2 - 8
        self.color = QColor.fromHsvF(math.atan2(y, x) / (2 * math.pi) % 1,
                                    min(1, math.hypot(x, y) / radius), self.color.valueF())
        self.update()
        self.changed.emit(self.color)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.choose(event.position())

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.choose(event.position())


class ColorEditor(QWidget):
    changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.wheel = Wheel()
        self.wheel.changed.connect(self.choose)
        layout.addWidget(self.wheel)
        row = QHBoxLayout()
        row.addWidget(QLabel("HEX"))
        self.hex = QLineEdit("#FFFFFF")
        self.hex.setMaxLength(7)
        self.hex.editingFinished.connect(self.enter)
        row.addWidget(self.hex)
        layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("亮度"))
        self.value = QSlider(Qt.Orientation.Horizontal)
        self.value.setRange(0, 255)
        self.value.setValue(255)
        self.value.valueChanged.connect(self.brightness)
        row.addWidget(self.value, 1)
        layout.addLayout(row)

    def set_color(self, text):
        self.set_value(QColor(text))

    def set_value(self, value):
        self.wheel.color = value
        self.wheel.update()
        self.hex.setText(value.name().upper())
        self.hex.setStyleSheet("")
        self.value.blockSignals(True)
        self.value.setValue(value.value())
        self.value.blockSignals(False)

    def choose(self, value):
        self.set_value(value)
        self.changed.emit(value.name().upper())

    def enter(self):
        text = self.hex.text().strip()
        text = text if text.startswith("#") else "#" + text
        value = QColor(text)
        if value.isValid() and len(text) == 7:
            self.choose(value)
        else:
            self.hex.setStyleSheet("border: 1px solid #df4141;")

    def brightness(self, value):
        old = self.wheel.color
        color = QColor.fromHsvF(max(0, old.hueF()), old.saturationF(), value / 255)
        self.choose(color)
