"""Aligned magnetic totals with separate integer, decimal and unit columns."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QGridLayout, QLabel, QWidget


class MomentSummary(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setObjectName('moment_summary')
        self.setStyleSheet('#moment_summary { background: rgba(255,255,255,215); } QLabel { color: black; font-size: 14px; }')
        layout = QGridLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setHorizontalSpacing(0)
        layout.setVerticalSpacing(0)
        font = QFont('Consolas')
        font.setPixelSize(14)
        self.fields, self._text = [], ''
        for row in range(3):
            fields = [QLabel() for _ in range(4)]
            for column, field in enumerate(fields):
                layout.addWidget(field, row, column)
            fields[1].setFont(font)
            fields[2].setFont(font)
            fields[0].setContentsMargins(0, 0, 4, 0)
            fields[3].setContentsMargins(4, 0, 0, 0)
            fields[1].setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.fields.append(fields)

    def text(self):
        return self._text

    def setText(self, text):
        self._text = text
        for fields, line in zip(self.fields, text.splitlines()):
            title, value = line.split('：', 1)
            number = value.removesuffix(' μB')
            integer, dot, decimals = number.partition('.')
            for field, part in zip(fields, (title + '：', integer, dot + decimals, 'μB' if value.endswith(' μB') else '')):
                field.setText(part)
