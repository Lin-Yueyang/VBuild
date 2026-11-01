"""Movable controls for cached OUTCAR spin moments."""

from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtWidgets import QComboBox, QFormLayout, QHeaderView, QHBoxLayout, QLabel, QPushButton, QSpinBox, QTableView, QToolButton, QVBoxLayout, QWidget

from .element_filter import ElementFilter
from .overlay import Overlay
from .report_panel import Table


class MomentPanel(Overlay):
    changed = Signal()
    reload_requested = Signal()
    table_requested = Signal(bool)

    def __init__(self, parent):
        super().__init__(parent)
        self.setFixedWidth(285)
        self.setObjectName("moment_panel")
        self.setStyleSheet("#moment_panel { background: white; border: 1px solid #bbc3cc; }")
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        self.grip.setText("磁矩")
        header.addWidget(self.grip, 1)
        close = QToolButton()
        close.setText("×")
        close.clicked.connect(self.hide)
        header.addWidget(close)
        layout.addLayout(header)
        form = QFormLayout()
        self.orbital = QComboBox()
        self.orbital.setObjectName("moment_orbital")
        self.component = QComboBox()
        self.decimals = QSpinBox()
        self.decimals.setRange(0, 6)
        self.decimals.setValue(2)
        form.addRow("轨道分量", self.orbital)
        form.addRow("自旋分量", self.component)
        form.addRow("显示小数位", self.decimals)
        layout.addLayout(form)
        self.info = QLabel("正在读取 OUTCAR…")
        self.info.setWordWrap(True)
        self.info.setToolTip('前两行是当前轨道和自旋分量的局域投影合计：已显示原子、全部原子。\n'
                             '筛选只改变第一行，周期镜像不重复计数。\n'
                             '第三行来自 OUTCAR 的全胞自旋总值，可以与局域投影合计不同。')
        layout.addWidget(self.info)
        self.toggle = QPushButton("显示原子磁矩")
        self.toggle.setCheckable(True)
        layout.addWidget(self.toggle)
        self.elements = ElementFilter(self)
        layout.addWidget(self.elements)
        self.table = QPushButton("查看原始数据")
        self.table.setCheckable(True)
        self.table.toggled.connect(self.table_requested)
        layout.addWidget(self.table)
        self.reload = QPushButton("重新读取")
        self.reload.clicked.connect(self.reload_requested)
        layout.addWidget(self.reload)
        for signal in (self.orbital.currentIndexChanged, self.component.currentIndexChanged, self.decimals.valueChanged, self.toggle.toggled, self.elements.changed):
            signal.connect(self.changed)
        self.hide()

    def populate(self, data, shown, valid, busy, table=False):
        blockers = [QSignalBlocker(widget) for widget in (self.orbital, self.component, self.decimals, self.toggle, self.table)]
        orbitals = ("tot",) if data is None else data.orbital_names
        if tuple(self.orbital.itemData(i) for i in range(self.orbital.count())) != orbitals:
            self.orbital.clear()
            for key in orbitals:
                self.orbital.addItem("总磁矩（tot）" if key == "tot" else key, key)
        vector = data is not None and data.moments.shape[1] == 3
        choices = (("模长 |m|", "norm"), ("x", "x"), ("y", "y"), ("z", "z")) if vector else (("磁矩（有符号）", "scalar"),)
        if tuple(self.component.itemData(i) for i in range(self.component.count())) != tuple(value for _name, value in choices):
            self.component.clear()
            for name, value in choices:
                self.component.addItem(name, value)
        if data is not None:
            self.orbital.setCurrentIndex(self.orbital.findData(data.orbital))
            self.component.setCurrentIndex(self.component.findData(data.component))
            self.decimals.setValue(data.decimals)
            self.elements.populate(data.symbols, data.hidden_elements)
        self.toggle.setChecked(shown)
        self.table.setChecked(table)
        self.table.setEnabled(data is not None)
        self.toggle.setText("关闭原子磁矩" if shown else "显示原子磁矩")
        for widget in (self.toggle, self.orbital, self.component, self.decimals, self.elements):
            widget.setEnabled(data is not None and not busy)
        self.toggle.setEnabled(valid and not busy)
        self.reload.setEnabled(not busy)
        text = "正在后台读取 OUTCAR…" if busy else "未载入磁矩。" if data is None else (
            f"{data.path.name} · 第 {data.step} 个离子步" +
            ("\n当前几何与磁矩对应结构不一致，标记已暂停。" if not valid else ""))
        self.info.setText(text)
        self.component.setToolTip(f"x/y/z 为 VASP 自旋坐标；SAXIS={data.saxis}" if vector else "")
        self.adjustSize()
        del blockers


class MomentTable(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.caption = QLabel()
        self.caption.setWordWrap(True)
        layout.addWidget(self.caption)
        self.table = QTableView()
        self.model = Table(self.table)
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        header.setSectionsMovable(True)
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setResizeContentsPrecision(64)
        layout.addWidget(self.table, 1)
        self.key = None

    def populate(self, data, valid):
        key = (id(data), data.orbital, data.component, valid)
        if key == self.key:
            return
        self.key = key
        vector = data.moments.shape[1] == 3
        channels = [*data.orbitals.items(), ("tot", data.moments)]
        titles = [name + (" " + axis if vector else "") for name, _values in channels
                  for axis in ("xyz" if vector else ("",))]
        selected = data.values()
        rows = [[index + 1, symbol, *(float(value) for _name, values in channels for value in values[index]), float(selected[index])]
                for index, symbol in enumerate(data.symbols)]
        current = data.orbital + (" |m|" if data.component == "norm" else " " + data.component if vector else "")
        self.model.populate(["序号", "元素", *titles, "当前显示值"], rows)
        self.caption.setText(f"{data.path}\n第 {data.step} 个离子步 · 磁矩单位 μB · 当前显示：{current}\n"
                             "s/p/d/f 与 tot 保留文件原值，当前显示值按所选分量计算。" +
                             ("\n当前几何不匹配；此处仍为源文件对应步骤的原始数据。" if not valid else ""))
