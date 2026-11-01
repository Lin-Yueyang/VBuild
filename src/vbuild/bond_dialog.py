"""Editable bond distance ranges for element pairs in the current structure."""

from itertools import combinations_with_replacement

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QHBoxLayout,
                              QHeaderView, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .bonds import default_maximum, pair_key


class BondDialog(QDialog):
    applied = Signal(object)

    def __init__(self, symbols, factor, ranges, parent):
        super().__init__(parent)
        self.setWindowTitle("显示键长范围")
        self.resize(620, 360)
        layout = QVBoxLayout(self)
        pairs = list(combinations_with_replacement(sorted(set(symbols)), 2))
        table = QTableWidget(len(pairs), 5)
        table.setObjectName("bond_ranges")
        table.setHorizontalHeaderLabels(["元素对", "默认范围", "自定义", "最短", "最长"])
        self.rows = []
        for row, (first, second) in enumerate(pairs):
            key = pair_key(first, second)
            maximum = default_maximum(first, second, factor)
            for column, text in enumerate((key, f"0.00–{maximum:.2f}")):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                table.setItem(row, column, item)
            custom = QCheckBox()
            custom.setChecked(key in ranges)
            container = QWidget()
            centered = QHBoxLayout(container)
            centered.setContentsMargins(0, 0, 0, 0)
            centered.addWidget(custom, 0, Qt.AlignmentFlag.AlignCenter)
            table.setCellWidget(row, 2, container)
            bounds = []
            values = ranges[key] if key in ranges else (0, maximum)
            for column, number in zip((3, 4), values):
                spin = QDoubleSpinBox()
                spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
                spin.setDecimals(2)
                spin.setRange(0, 100)
                spin.setSingleStep(0.05)
                spin.setValue(number)
                spin.setEnabled(custom.isChecked())
                custom.toggled.connect(spin.setEnabled)
                table.setCellWidget(row, column, spin)
                bounds.append(spin)
            bounds[0].setMaximum(values[1])
            bounds[1].setMinimum(values[0])
            bounds[0].valueChanged.connect(lambda number, upper=bounds[1]: upper.setMinimum(number))
            bounds[1].valueChanged.connect(lambda number, lower=bounds[0]: lower.setMaximum(number))
            self.rows.append((key, custom, bounds, tuple(values)))
        table.resizeColumnsToContents()
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setStretchLastSection(False)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        table.setColumnWidth(0, 100)
        table.setColumnWidth(1, 150)
        layout.addWidget(table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.button(QDialogButtonBox.StandardButton.Apply).setText("应用")
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.apply)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.original = dict(ranges)

    def apply(self):
        self.applied.emit(self.ranges())

    def accept(self):
        self.apply()
        super().accept()

    def ranges(self):
        result = self.original.copy()
        for key, custom, bounds, initial in self.rows:
            if custom.isChecked():
                result[key] = [number if spin.value() == round(number, 2) else spin.value()
                               for spin, number in zip(bounds, initial)]
            else:
                result.pop(key, None)
        return result
