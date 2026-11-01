"""Compact element and synchronized coordinate editors."""

import numpy as np
from ase.data import chemical_symbols
from PySide6.QtCore import QLocale, QSignalBlocker, Signal, Qt
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from .colors import element_colors


class PeriodicTable(QDialog):
    def __init__(self, symbol: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择元素")
        self.symbol = symbol
        grid = QGridLayout(self)
        grid.setSpacing(3)
        periods = (
            (1, 2), (3, 4, *range(5, 11)), (11, 12, *range(13, 19)),
            tuple(range(19, 37)), tuple(range(37, 55)),
            (55, 56, 57, *range(72, 87)), (87, 88, 89, *range(104, 119)),
        )
        for row, elements in enumerate(periods):
            columns = (0, 17) if row == 0 else (0, 1, *range(12, 18)) if row < 3 else range(18)
            for column, element in zip(columns, elements):
                self._button(grid, element, row, column)
        grid.addWidget(QLabel("镧系"), 8, 0, 1, 2)
        grid.addWidget(QLabel("锕系"), 9, 0, 1, 2)
        for row, elements in ((8, range(58, 72)), (9, range(90, 104))):
            for column, element in enumerate(elements, 3):
                self._button(grid, element, row, column)

    def _button(self, grid, element: int, row: int, column: int):
        symbol = chemical_symbols[element]
        button = QPushButton(f"{element}\n{symbol}")
        button.setObjectName(symbol)
        button.setFixedSize(43, 44)
        button.setCheckable(True)
        button.setChecked(symbol == self.symbol)
        red, green, blue = (element_colors[element] * 255).astype(int)
        ink = "#111" if element_colors[element].mean() > 0.45 else "#fff"
        button.setStyleSheet(f"QPushButton {{ background: rgb({red},{green},{blue}); color: {ink}; border: 1px solid #aaa; }}"
                            "QPushButton:checked { border: 3px solid #2779be; } QPushButton:hover { border: 2px solid #2779be; }")
        button.clicked.connect(lambda _checked, value=symbol: self._choose(value))
        grid.addWidget(button, row, column)

    def _choose(self, symbol: str):
        self.symbol = symbol
        self.accept()


class ElementButton(QPushButton):
    symbol_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("element")
        self.symbol = ""
        self.set_symbol("H")
        self.clicked.connect(self.choose)

    def set_symbol(self, symbol: str):
        changed = symbol != self.symbol
        self.symbol = symbol
        self.setText(symbol + " · 选择元素")
        if changed:
            self.symbol_changed.emit(symbol)

    def choose(self):
        dialog = PeriodicTable(self.symbol, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.set_symbol(dialog.symbol)


class Coordinates(QWidget):
    valid_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cell = np.eye(3)
        self.position = np.zeros(3)
        self.fraction = np.zeros(3)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        for column, label in enumerate(("", "笛卡尔 (Å)", "分数")):
            grid.addWidget(QLabel(label), 0, column)
        for row, axis in enumerate("xyz", 1):
            grid.addWidget(QLabel(axis), row, 0)
        self.fields = {}
        for column, mode in enumerate(("cart", "frac"), 1):
            fields = []
            for row, axis in enumerate("xyz", 1):
                field = QLineEdit()
                field.setObjectName(axis if mode == "cart" else "f" + axis)
                field.setMinimumWidth(65)
                validator = QDoubleValidator(field)
                validator.setLocale(QLocale.c())
                validator.setNotation(QDoubleValidator.Notation.ScientificNotation)
                field.setValidator(validator)
                field.textChanged.connect(lambda _text, m=mode, a=row - 1: self._edit(m, a))
                grid.addWidget(field, row, column)
                fields.append(field)
            self.fields[mode] = fields

    def set_position(self, cell: np.ndarray, position: np.ndarray):
        self.cell = None if cell is None else cell.copy()
        self.position = position.copy()
        self.fraction = None if cell is None else np.linalg.solve(cell.T, position)
        self._display("cart", self.position)
        if cell is not None:
            self._display("frac", self.fraction)
        for field in self.fields["frac"]:
            field.setEnabled(cell is not None)
            if cell is None:
                with QSignalBlocker(field):
                    field.setText("—")
        self.valid_changed.emit(True)

    def _display(self, mode: str, values: np.ndarray):
        for field, value in zip(self.fields[mode], values):
            with QSignalBlocker(field):
                field.setText(f"{value:.5f}")

    def _edit(self, mode: str, axis: int):
        if not self.fields[mode][axis].hasAcceptableInput():
            self.valid_changed.emit(False)
            return
        value = float(self.fields[mode][axis].text())
        if mode == "cart":
            self.position[axis] = value
            if self.cell is not None:
                self.fraction = np.linalg.solve(self.cell.T, self.position)
                self._display("frac", self.fraction)
        else:
            self.fraction[axis] = value
            self.position = self.fraction @ self.cell
            self._display("cart", self.position)
        self.valid_changed.emit(all(field.hasAcceptableInput()
                                    for fields in self.fields.values() for field in fields if field.isEnabled()))


class AtomFields(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QFormLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.element = ElementButton(self)
        self.coordinates = Coordinates(self)
        layout.addRow("元素", self.element)
        layout.addRow(self.coordinates)

    def populate(self, symbol: str, cell: np.ndarray, position: np.ndarray):
        with QSignalBlocker(self.element):
            self.element.set_symbol(symbol)
        self.coordinates.set_position(cell, position)


def cell_text(cell: np.ndarray) -> str:
    if cell is None:
        return "无周期性晶胞；坐标单位 Å。"
    rows = ["<tr><th></th><th>x</th><th>y</th><th>z</th></tr>"]
    rows += [f"<tr><td>{axis}</td>" + "".join(f"<td>{value:.3f}</td>" for value in vector) + "</tr>"
             for axis, vector in zip("abc", cell)]
    return '晶胞矢量 (Å)<table cellpadding="3">' + "".join(rows) + "</table>"


class AtomDialog(QDialog):
    def __init__(self, symbol: str, cell: np.ndarray, position: np.ndarray, parent=None):
        super().__init__(parent)
        self.setWindowTitle("插入原子")
        layout = QVBoxLayout(self)
        self.fields = AtomFields(self)
        self.fields.populate(symbol, cell, position)
        layout.addWidget(self.fields)
        cell_label = QLabel(cell_text(cell))
        cell_label.setObjectName("cell")
        layout.addWidget(cell_label)
        layout.addWidget(QLabel("新原子默认 T T T；同元素插入该组末尾。"))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.fields.coordinates.valid_changed.connect(buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class AtomPanel(QFrame):
    edit_shortcut = 'E'
    apply_requested = Signal(str, object)
    element_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("atom_panel")
        self.setStyleSheet("QFrame#atom_panel { background: #f5f6f8; border: 1px solid #a5afba; border-radius: 4px; }")
        layout = QVBoxLayout(self)
        self.title = QLabel()
        self.fields = AtomFields(self)
        self.fields.element.symbol_changed.connect(self.element_requested)
        apply = QPushButton("应用")
        apply.setObjectName("apply_atom")
        self.apply = apply
        apply.clicked.connect(lambda: self.apply_requested.emit(
            self.fields.element.symbol, self.fields.coordinates.position.copy()))
        self.fields.coordinates.valid_changed.connect(apply.setEnabled)
        layout.addWidget(self.title)
        layout.addWidget(self.fields)
        layout.addWidget(apply)
        self.move(10, 10)
        self.hide()

    def populate(self, index: int, symbol: str, cell: np.ndarray, position: np.ndarray, *, editable=True):
        self.title.setText(f"原子 #{index + 1} · {symbol}")
        self.fields.populate(symbol, cell, position)
        self.fields.element.setEnabled(editable)
        hint = '' if editable else '请用编辑模式' + (f'({self.edit_shortcut})' if self.edit_shortcut else '') + '修改'
        self.fields.element.setToolTip(hint)
        for mode, fields in self.fields.coordinates.fields.items():
            for field in fields:
                field.setReadOnly(not editable)
                field.setEnabled(editable and (mode == "cart" or cell is not None))
                field.setToolTip(hint)
                field.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips, True)
        self.apply.setVisible(editable)
        self.adjustSize()
        self.show()
        self.raise_()
