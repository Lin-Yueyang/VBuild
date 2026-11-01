"""Energy records and current-frame raw force components in the existing right sidebar."""

import numpy as np
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtWidgets import QFrame, QHeaderView, QLabel, QTableView, QTabWidget, QVBoxLayout

FIELDS = (("energy", "能量 (eV)"), ("free_energy", "自由能 (eV)"), ("e_0_energy", "E₀ (eV)"),
          ("e_fr_energy", "自由能 Efr (eV)"), ("e_wo_entrp", "无熵能量 (eV)"),
          ("max_force", "最大力 (eV/Å)"), ("temperature", "温度 (K)"),
          ("time", "文件时间"), ("timestep", "LAMMPS step"))


class Table(QAbstractTableModel):
    def __init__(self, parent):
        super().__init__(parent)
        self.rows, self.titles = [], []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.titles)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole:
            return self.titles[section] if orientation == Qt.Orientation.Horizontal else str(section + 1)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return Qt.AlignmentFlag.AlignCenter
        if role == Qt.ItemDataRole.DisplayRole:
            value = self.rows[index.row()][index.column()]
            return "—" if value is None else f"{value:.6g}" if isinstance(value, (float, np.floating)) else str(value)

    def populate(self, titles, rows):
        self.beginResetModel()
        self.titles, self.rows = titles, rows
        self.endResetModel()


class ReportPanel(QFrame):
    frame_requested = Signal(int)

    def __init__(self, parent):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.caption = QLabel()
        self.records = None
        layout.addWidget(self.caption)
        tabs = QTabWidget()
        self.tables, self.models = {}, {}
        for key, title in (("energy", "逐帧能量"), ("forces", "当前帧原子力")):
            table = QTableView()
            model = ForceTable(table) if key == "forces" else Table(table)
            table.setModel(model)
            table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
            table.horizontalHeader().setResizeContentsPrecision(64)
            table.setAlternatingRowColors(True)
            self.tables[key], self.models[key] = table, model
            tabs.addTab(table, title)
        self.tables["energy"].doubleClicked.connect(lambda index: self.frame_requested.emit(index.row()))
        layout.addWidget(tabs, 1)
        self.hide()

    def clear(self):
        if self.records is None:
            return
        self.models["energy"].populate([], [])
        self.models["forces"].populate((), None)
        self.records = None

    def populate(self, records, document, frame):
        self.caption.setText(f"文件原始数据 · 当前帧 {frame + 1} · 双击能量行切帧\n能量单位 eV；力单位 eV/Å，不应用固定约束。")
        if records is not self.records:
            fields = [(key, label) for key, label in FIELDS if any(key in row for row in records)]
            self.models["energy"].populate(["帧", *(label for _key, label in fields)],
                [[index + 1, *(row.get(key) for key, _label in fields)] for index, row in enumerate(records)])
            self.records = records
        forces = getattr(document, "forces", None)
        self.models["forces"].populate(document.structure.symbols, forces)


class ForceTable(Table):
    def __init__(self, parent):
        super().__init__(parent)
        self.symbols, self.forces = (), None
        self.titles = ["原子", "Fx (eV/Å)", "Fy (eV/Å)", "Fz (eV/Å)", "|F| (eV/Å)"]

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() or self.forces is None else len(self.forces)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return Qt.AlignmentFlag.AlignCenter
        if role == Qt.ItemDataRole.DisplayRole:
            row, column = index.row(), index.column()
            if column == 0:
                return f"{self.symbols[row]} {row + 1}"
            value = self.forces[row, column - 1] if column < 4 else np.linalg.norm(self.forces[row])
            return f"{value:.6g}"

    def populate(self, symbols, forces):
        self.beginResetModel()
        self.symbols, self.forces = symbols, forces
        self.endResetModel()
