"""Movable Bader controls and a read-only table in the page's analysis tabs."""

import numpy as np
from ase.data import atomic_numbers
from PySide6.QtCore import QSignalBlocker, Signal, Qt
from PySide6.QtWidgets import (QComboBox, QFormLayout, QHBoxLayout, QLabel, QPushButton, QSpinBox,
                              QTableView, QToolButton, QVBoxLayout, QWidget, QHeaderView)

from .overlay import Overlay
from .element_filter import ElementFilter
from .report_panel import Table


class BaderPanel(Overlay):
    changed = Signal()
    table_requested = Signal(bool)

    def __init__(self, parent):
        super().__init__(parent)
        self.setFixedWidth(285)
        self.setObjectName("bader_panel")
        self.setStyleSheet("#bader_panel { background: white; border: 1px solid #bbc3cc; }")
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        self.grip.setText("Bader 电荷")
        header.addWidget(self.grip, 1)
        close = QToolButton()
        close.setText("×")
        close.clicked.connect(self.hide)
        header.addWidget(close)
        layout.addLayout(header)
        form = QFormLayout()
        self.mode = QComboBox()
        self.mode.addItem("价电子净电荷（ZVAL − N）", "valence")
        self.mode.addItem("全电子净电荷（Z − N）", "all")
        self.mode.addItem("ACF 原始电子数", "electrons")
        self.mode.setToolTip("默认适用于 bader CHGCAR -ref CHGCAR_sum；只有积分全电子密度时才选全电子。")
        self.decimals = QSpinBox()
        self.decimals.setRange(0, 6)
        form.addRow("数据类型", self.mode)
        form.addRow("显示小数位", self.decimals)
        layout.addLayout(form)
        self.info = QLabel()
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        self.toggle = QPushButton("显示原子电荷")
        self.toggle.setCheckable(True)
        self.table = QPushButton("查看原始数据")
        self.table.setCheckable(True)
        self.table.toggled.connect(self.table_requested)
        layout.addWidget(self.toggle)
        self.elements = ElementFilter(self)
        self.elements.changed.connect(self.changed)
        layout.addWidget(self.elements)
        layout.addWidget(self.table)
        self.note = QLabel("净电荷正值：失电子；负值：得电子。\nACF.dat 不记录所用密度类型，请按计算选择。")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.mode.currentIndexChanged.connect(self.changed)
        self.decimals.valueChanged.connect(self.changed)
        self.toggle.toggled.connect(self.changed)
        self.hide()

    def populate(self, data, shown, table, valid):
        blockers = [QSignalBlocker(widget) for widget in (self.mode, self.decimals, self.toggle, self.table)]
        self.mode.setCurrentIndex(self.mode.findData(data.mode))
        self.decimals.setValue(data.decimals)
        available = data.charges() is not None
        self.toggle.setEnabled(valid and available)
        self.toggle.setChecked(shown)
        self.toggle.setText("关闭原子电荷" if shown else "显示原子电荷")
        self.table.setChecked(table)
        self.elements.populate(data.symbols, data.hidden_elements)
        self.info.setText("、".join(data.files) + "\n" + data.baseline +
                          ("\n当前几何已改变，电荷标记已暂停。" if not valid else "") +
                          ("\n可选择原始电子数查看；不要以普通价态代替 ZVAL。" if not available else ""))
        self.adjustSize()
        del blockers


class BaderModel(Table):
    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole:
            value = self.rows[index.row()][index.column()]
            if isinstance(value, (float, np.floating)):
                return f"{value:.6f}"
        return super().data(index, role)


class BaderTable(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.caption = QLabel()
        self.caption.setWordWrap(True)
        layout.addWidget(self.caption)
        self.table = QTableView()
        self.model = BaderModel(self.table)
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionsMovable(True)
        self.table.horizontalHeader().setResizeContentsPrecision(64)
        layout.addWidget(self.table, 1)
        self.data_key = None
        self.hide()

    def populate(self, data, valid):
        key = (id(data), data.mode, valid)
        if key == self.data_key:
            return
        first = self.data_key is None
        self.data_key = key
        self.caption.setText(str(data.path) + "\n" + data.summary +
                             ("\n当前几何已改变；表格仍为源文件的数据。" if not valid else ""))
        charges = data.charges()
        neutral = data.neutral if data.mode != "all" else np.array([atomic_numbers[s] for s in data.symbols])
        rows = [[int(row[0]), symbol, *row[1:], None if neutral is None else neutral[i],
                 None if charges is None or data.mode == "electrons" else charges[i]]
                for i, (symbol, row) in enumerate(zip(data.symbols, data.rows))]
        self.model.populate(["编号", "元素", "X (Å)", "Y (Å)", "Z (Å)", "CHARGE (电子数)",
                             "MIN DIST (Å)", "ATOMIC VOL (Å³)", "基准电子数", "实际电荷 (e)"], rows)
        if first:
            self.table.horizontalHeader().moveSection(9, 2)
