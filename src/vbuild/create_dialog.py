"""New structure choices, cell matrix, carbon templates and MP search pages."""

import sys

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                              QGridLayout, QHBoxLayout, QLabel, QLineEdit, QProgressBar, QPushButton,
                              QRadioButton, QSpinBox, QSplitter, QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .atoms import PeriodicTable
from .catalog import carbon
from .draft import DraftDocument
from .materials import MaterialsClient, PAGE_SIZE, structure_poscar, summary_query
from .mp_key import save as save_key
from .markup import RichDelegate, formula_html, spacegroup_html
from .scene import Scene


def integer(value, minimum=1):
    spin = QSpinBox()
    spin.setRange(minimum, 1000)
    spin.setValue(value)
    return spin


def decimal(value, step=0.01):
    spin = QDoubleSpinBox()
    spin.setRange(0.01, 10000)
    spin.setDecimals(2)
    spin.setSingleStep(step)
    spin.setValue(value)
    spin.setSuffix(" Å")
    return spin


class BlankDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("空白结构 · 晶胞")
        layout = QVBoxLayout(self)
        self.periodic = QCheckBox("设置周期性边界")
        self.periodic.setChecked(True)
        layout.addWidget(self.periodic)
        self.matrix = QWidget()
        grid = QGridLayout(self.matrix)
        self.values = []
        for column, label in enumerate(("", "x (Å)", "y (Å)", "z (Å)")):
            grid.addWidget(QLabel(label), 0, column)
        for row, axis in enumerate("abc"):
            grid.addWidget(QLabel(axis), row + 1, 0)
            values = []
            for column in range(3):
                spin = QDoubleSpinBox()
                spin.setRange(-100000, 100000)
                spin.setDecimals(3)
                spin.setValue(15 if row == column else 0)
                grid.addWidget(spin, row + 1, column + 1)
                values.append(spin)
            self.values.append(values)
        layout.addWidget(self.matrix)
        self.periodic.toggled.connect(self.matrix.setVisible)
        self.error = QLabel()
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("创建")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.create)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def create(self):
        cell = np.array([[value.value() for value in row] for row in self.values]) if self.periodic.isChecked() else None
        try:
            self.document = DraftDocument.blank(cell)
        except ValueError as error:
            self.error.setText(str(error))
            return
        self.accept()


class MaterialsPage(QWidget):
    material_ready = Signal(bytes, str)
    key_changed = Signal(str)

    def __init__(self, key, parent):
        super().__init__(parent)
        self.client = MaterialsClient(self)
        self.client.ready.connect(self.received)
        self.client.failed.connect(self.failed)
        self.client.busy_changed.connect(self.busy)
        self.records, self.offset, self.total, self.job, self.symbol = [], 0, 0, "search", ""
        self.search_symbol, self.saved_key = "", key.strip()
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("API key"))
        self.key = QLineEdit(key)
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("MP_API_KEY")
        self.key.setToolTip('输入后保存到 Mac 钥匙串。' if sys.platform == 'darwin' else "Windows：输入后自动保存到用户环境变量 MP_API_KEY。")
        self.key.editingFinished.connect(self.remember_key)
        row.addWidget(self.key, 1)
        link = QLabel('<a href="https://materialsproject.org/dashboard">获取 key</a>')
        link.setOpenExternalLinks(True)
        row.addWidget(link)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.formula = QLineEdit()
        self.formula.setObjectName("material_formula")
        self.formula.setMinimumHeight(34)
        self.formula.setPlaceholderText("元素或化学式")
        self.formula.returnPressed.connect(self.search)
        row.addWidget(self.formula, 1)
        self.element = QPushButton("元素周期表")
        self.element.clicked.connect(self.choose_element)
        row.addWidget(self.element)
        self.search_button = QPushButton("搜索")
        self.search_button.clicked.connect(self.search)
        row.addWidget(self.search_button)
        layout.addLayout(row)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["选择", "编号", "化学式", "原子数", "空间群", "晶体系统", "凸包能 (eV/原子)", "带隙 (eV)"])
        self.table.verticalHeader().hide()
        self.table.setItemDelegate(RichDelegate(self.table))
        self.radios = []
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self.update_open)
        self.table.cellDoubleClicked.connect(lambda *_args: self.download())
        layout.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.previous, self.next = QPushButton("上一页"), QPushButton("下一页")
        self.previous.clicked.connect(lambda: self.search_page(self.offset - PAGE_SIZE))
        self.next.clicked.connect(lambda: self.search_page(self.offset + PAGE_SIZE))
        row.addWidget(self.previous)
        self.page = QLabel("未搜索")
        row.addWidget(self.page, 1)
        row.addWidget(self.next)
        layout.addLayout(row)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.status = QLabel("输入化学式或从周期表选择元素。")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.open = QPushButton("下载 POSCAR 并打开")
        self.open.clicked.connect(self.download)
        layout.addWidget(self.open)
        self.busy(False)

    def choose_element(self):
        dialog = PeriodicTable(self.symbol, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.symbol = dialog.symbol
            self.formula.setText(self.symbol)
            self.search()

    def search(self):
        if not self.remember_key():
            return
        self.search_symbol = self.formula.text().strip()
        self.search_page(0)

    def remember_key(self):
        key = self.key.text().strip()
        if not key or key == self.saved_key:
            return True
        try:
            persistent = save_key(key)
        except (OSError, ValueError) as error:
            self.status.setText("API key 保存失败：" + str(error))
            return False
        self.saved_key = key
        self.key_changed.emit(key)
        self.status.setText('API key 已保存到 Mac 钥匙串。' if persistent and sys.platform == 'darwin' else
                           "API key 已保存到用户环境变量。" if persistent else "API key 已用于本次运行。")
        return True

    def search_page(self, offset):
        self.offset, self.job = offset, "search"
        self.records = []
        self.radios = []
        self.table.setRowCount(0)
        self.status.setText("正在搜索 " + self.search_symbol)
        try:
            params = summary_query(self.search_symbol, offset)
        except ValueError as error:
            self.failed(str(error))
            return
        self.client.fetch(params, self.key.text())

    def download(self):
        if self.client.reply is not None or self.table.currentRow() < 0:
            return
        self.material_id = self.records[self.table.currentRow()]["material_id"]
        self.job = "structure"
        self.status.setText("正在下载 " + self.material_id)
        self.client.fetch({"material_ids": self.material_id, "_fields": "material_id,structure", "_limit": 1}, self.key.text())

    def received(self, result):
        records, total = result
        try:
            if self.job == "structure":
                if len(records) != 1 or records[0]["material_id"] != self.material_id:
                    raise ValueError("API 未返回所选材料的结构。")
                self.material_ready.emit(structure_poscar(records[0]), self.material_id)
                return
            self.records, self.total = records, total
            self.radios = []
            self.table.setRowCount(len(records))
            for row, record in enumerate(records):
                widget = QWidget()
                layout = QHBoxLayout(widget)
                layout.setContentsMargins(0, 0, 0, 0)
                radio = QRadioButton()
                radio.setAutoExclusive(False)
                radio.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                layout.addWidget(radio, 0, Qt.AlignmentFlag.AlignCenter)
                self.radios.append(radio)
                self.table.setCellWidget(row, 0, widget)
                self.table.setItem(row, 0, QTableWidgetItem())
                symmetry = record["symmetry"]
                values = [record["material_id"], record["formula_pretty"], record["nsites"],
                          None if symmetry is None else symmetry["symbol"], None if symmetry is None else symmetry["crystal_system"],
                          record["energy_above_hull"], record["band_gap"]]
                for column, value in enumerate(values, 1):
                    text = "—" if value is None else f"{value:.3f}" if isinstance(value, float) else str(value)
                    item = QTableWidgetItem(text)
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    if value is not None and column in (2, 4):
                        item.setData(Qt.ItemDataRole.UserRole, formula_html(text) if column == 2 else spacegroup_html(text))
                    self.table.setItem(row, column, item)
            self.table.resizeColumnsToContents()
            padding = self.table.fontMetrics().horizontalAdvance("0") * 4
            for column in range(8):
                self.table.setColumnWidth(column, self.table.columnWidth(column) + padding)
            self.table.resizeRowsToContents()
            self.page.setText(f"{self.offset + 1 if records else 0}–{self.offset + len(records)} / {total}")
            self.status.setText("选择一行下载对应结构。" if records else "没有符合条件的结构。")
            self.busy(False)
        except (ValueError, TypeError, KeyError) as error:
            self.failed("下载数据格式错误：" + str(error))

    def failed(self, message):
        self.status.setText(message)
        self.busy(False)

    def update_open(self):
        self.open.setEnabled(self.client.reply is None and self.table.currentRow() >= 0)
        for index, radio in enumerate(self.radios):
            radio.setChecked(index == self.table.currentRow())

    def busy(self, value):
        self.progress.setVisible(value)
        for widget in (self.key, self.formula, self.element, self.search_button, self.table):
            widget.setEnabled(not value)
        self.previous.setEnabled(not value and self.offset > 0)
        self.next.setEnabled(not value and bool(self.records) and self.offset + len(self.records) < self.total)
        self.update_open()


class ConstructDialog(QDialog):
    def __init__(self, key, parent):
        super().__init__(parent)
        self.setWindowTitle("构建结构")
        self.document, self.content, self.material_id = None, None, None
        self.viewer = None
        layout = QVBoxLayout(self)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(7)
        self.tabs = QTabWidget()
        self.tabs.setMinimumWidth(260)
        self.materials = MaterialsPage(key, self)
        self.materials.material_ready.connect(self.select_material)
        self.tabs.addTab(self.materials, "Materials Project")
        self.fields = {}
        for kind, title, definitions in (
            ("graphene", "石墨烯", (("first", "a 方向重复", integer(3)), ("second", "b 方向重复", integer(3)),
                                  ("edge", "晶格边长 a", decimal(2.46)), ("space", "晶胞 c 高度", decimal(15, 1)))),
            ("tube", "碳纳米管", (("first", "手性指数 n", integer(6)), ("second", "手性指数 m", integer(6, 0)),
                                   ("repeat", "轴向重复", integer(1)), ("edge", "C–C 键长", decimal(1.42)),
                                   ("space", "横向空隙（单侧）", decimal(5, 1))))):
            page = QWidget()
            form = QFormLayout(page)
            fields = {}
            for name, label, widget in definitions:
                fields[name] = widget
                form.addRow(label, widget)
                widget.valueChanged.connect(self.changed)
            self.fields[kind] = fields
            self.tabs.addTab(page, title)
        self.splitter.addWidget(self.tabs)
        layout.addWidget(self.splitter, 1)
        self.error = QLabel()
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.error)
        footer = QHBoxLayout()
        self.hint = QLabel("请先预览")
        footer.addWidget(self.hint)
        self.preview = QPushButton("预览")
        self.preview.setObjectName("construct_preview")
        self.preview.clicked.connect(self.prepare)
        footer.addWidget(self.preview)
        footer.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.build = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.build.setText("构建")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.create)
        buttons.rejected.connect(self.reject)
        footer.addWidget(buttons)
        layout.addLayout(footer)
        self.tabs.currentChanged.connect(self.changed)
        self.finished.connect(self.finish)
        self.changed()
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
            button.setDefault(False)
        self.resize(960, 620)

    def keyPressEvent(self, event):
        if self.kind == "materials" and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            event.accept()
            return
        super().keyPressEvent(event)

    @property
    def kind(self):
        return ("materials", "graphene", "tube")[self.tabs.currentIndex()]

    def changed(self, *_args):
        self.document = None
        self.build.setEnabled(False)
        self.error.clear()
        template = self.kind != "materials"
        for widget in (self.hint, self.preview, self.build):
            widget.setVisible(template)
        if self.viewer is not None:
            self.viewer.clear_structure()
            self.viewer.setVisible(template)

    def prepare(self):
        values = {name: widget.value() for name, widget in self.fields[self.kind].items()}
        if self.kind == "graphene":
            values["repeat"] = 1
        try:
            self.document = carbon(self.kind, **values)
        except ValueError as error:
            self.error.setText(str(error))
            return
        if self.viewer is None:
            self.viewer = Scene(self)
            self.viewer.set_bond_radius(self.parent().settings["bond_radius"])
            self.viewer.setMinimumSize(330, 300)
            self.viewer.previewing = True
            self.splitter.addWidget(self.viewer)
            self.splitter.setSizes([350, 580])
        self.viewer.show()
        self.viewer.set_look("ball", view_style=self.parent().scene.visual.profile, rebuild=False)
        self.viewer.set_structure(self.document.structure, reset=True)
        structure = self.document.structure
        vectors = "\n".join(f"{axis}: " + "  ".join(f"{value:.3f}" for value in vector)
                            for axis, vector in zip("abc", structure.cell))
        detail = ""
        if self.kind == "tube":
            n, m, bond = values["first"], values["second"], values["edge"]
            diameter = np.sqrt(3) * bond * np.sqrt(n * n + n * m + m * m) / np.pi
            chiral = "非手性 · Zigzag" if m == 0 else "非手性 · Armchair" if n == m else "手性 · Chiral"
            angle = np.degrees(np.arctan2(np.sqrt(3) * m, 2 * n + m))
            detail = f"\n管径：{diameter:.3f} Å · {chiral} · 手性角：{angle:.2f}°"
        self.error.setText(f"碳原子数：{len(structure.symbols)}{detail}\n晶胞向量（Å）\n{vectors}")
        self.build.setEnabled(True)

    def create(self):
        if self.document is not None:
            self.accept()

    def finish(self, *_args):
        self.materials.client.cancel()
        if self.viewer is not None:
            self.viewer.plotter.close()

    def select_material(self, content, material_id):
        self.content, self.material_id = content, material_id
        self.accept()
