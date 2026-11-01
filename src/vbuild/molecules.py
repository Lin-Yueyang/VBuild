"""Separate molecule picker and cancellable, nonmodal water preview."""

from copy import deepcopy

from PySide6.QtCore import QEvent, QThread, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QGridLayout,
    QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from .catalog import MOLECULES
from .markup import FormulaButton
from .water import MASS_VOLUME, Region, density, free_volume, place_water


class MoleculeDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("添加分子")
        self.choice = None
        self.choices = QButtonGroup(self)
        self.choices.setExclusive(True)
        layout = QVBoxLayout(self)
        grid = QGridLayout()
        for index, (label, name) in enumerate(MOLECULES):
            button = FormulaButton(name)
            button.setObjectName("molecule_" + name)
            button.setToolTip(label)
            button.setMinimumSize(68, 36)
            self.add_choice(button, name)
            grid.addWidget(button, index // 5, index % 5)
        layout.addLayout(grid)
        methods = QGridLayout()
        for index, (name, label) in enumerate((("smiles", "以SMILES添加分子"), ("layer", "添加水层"), ("count", "以数量添加水分子"))):
            button = QPushButton(label)
            button.setObjectName("water_" + name)
            self.add_choice(button, name)
            methods.addWidget(button, index // 2, index % 2)
        methods.setColumnStretch(0, 1)
        methods.setColumnStretch(1, 1)
        layout.addLayout(methods)
        layout.addSpacing(16)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.setCenterButtons(True)
        self.confirm = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.confirm.setText("确定")
        self.confirm.setEnabled(False)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def add_choice(self, button, name):
        button.setCheckable(True)
        button.setAutoDefault(False)
        button.setProperty("choice", name)
        button.clicked.connect(lambda: self.choose(name))
        button.installEventFilter(self)
        self.choices.addButton(button)

    def choose(self, choice):
        self.choice = choice
        for button in self.choices.buttons():
            button.setChecked(button.property("choice") == choice)
        self.confirm.setEnabled(True)

    def eventFilter(self, watched, event):
        if (event.type() == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.LeftButton
                and watched in self.choices.buttons()):
            self.choose(watched.property("choice"))
            self.accept()
            return True
        return super().eventFilter(watched, event)


class WaterWorker(QThread):
    prepared = Signal(object)
    estimated = Signal(object)
    failed = Signal(str)
    progress = Signal(int)

    def __init__(self, structure, region, parameters, parent):
        super().__init__(parent)
        self.structure, self.region, self.parameters = structure, region, parameters

    def run(self):
        try:
            volume, resolution = free_volume(self.structure, self.region)
            self.estimated.emit((volume, resolution))
            if self.parameters is None:
                return
            group = place_water(self.structure, self.region, **self.parameters,
                                cancelled=self.isInterruptionRequested, progress=self.progress.emit)
            if group is not None:
                self.prepared.emit((group, volume, resolution))
        except ValueError as error:
            self.failed.emit(str(error))


class WaterDialog(QDialog):
    preview_requested = Signal(object, object)
    range_requested = Signal(object)
    invalidated = Signal()

    def __init__(self, kind, structure, parent=None):
        super().__init__(parent)
        self.setWindowTitle("添加水层" if kind == "layer" else "以数量添加水分子")
        self.kind, self.structure = kind, deepcopy(structure)
        self.prepared, self.worker = None, None
        self.closing = False
        self.initial = True
        self.free = None
        self.setMinimumWidth(430)
        layout = QVBoxLayout(self)
        self.form = QWidget()
        form = QFormLayout(self.form)
        self.lower, self.upper = QDoubleSpinBox(), QDoubleSpinBox()
        height = 15 if structure.cell is None else float(structure.cell[2, 2])
        for name, spin, value in (("water_lower", self.lower, 0), ("water_upper", self.upper, max(height, 0))):
            spin.setObjectName(name)
            spin.setRange(0, max(height, 0))
            spin.setDecimals(2)
            spin.setSingleStep(1)
            spin.setSuffix(" Å")
            spin.setValue(value)
            spin.valueChanged.connect(self.range_changed)
        limits = QHBoxLayout()
        limits.addWidget(self.lower)
        limits.addWidget(QLabel("至"))
        limits.addWidget(self.upper)
        form.addRow("z 范围", limits)
        self.counts = []
        if kind == "layer":
            row = QHBoxLayout()
            for axis in "xyz":
                spin = QSpinBox()
                spin.setObjectName("water_" + axis)
                spin.setRange(1, 100)
                spin.valueChanged.connect(self.changed)
                row.addWidget(QLabel(axis))
                row.addWidget(spin)
                self.counts.append(spin)
            form.addRow("各方向水数量", row)
        else:
            self.count = QSpinBox()
            self.count.setObjectName("water_count")
            self.count.setRange(1, 5000)
            self.count.valueChanged.connect(self.changed)
            form.addRow("水分子数量", self.count)
        self.factor = QDoubleSpinBox()
        self.factor.setObjectName("water_factor")
        self.factor.setRange(0.50, 1.00)
        self.factor.setDecimals(2)
        self.factor.setSingleStep(0.01)
        self.factor.setValue(0.65)
        self.factor.valueChanged.connect(self.changed)
        form.addRow("分子间范德华避碰系数", self.factor)
        self.seed = QSpinBox()
        self.seed.setObjectName("water_seed")
        self.seed.setRange(0, 1000000)
        self.seed.valueChanged.connect(self.changed)
        form.addRow("随机种子", self.seed)
        layout.addWidget(self.form)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        row = QHBoxLayout()
        self.boundary = QPushButton("预览范围")
        self.boundary.clicked.connect(self.show_range)
        row.addWidget(self.boundary)
        self.preview = QPushButton("预览水分子")
        self.preview.setObjectName("water_preview")
        self.preview.clicked.connect(self.prepare)
        row.addWidget(self.preview)
        layout.addLayout(row)
        self.estimate = QPushButton("估算推荐数量")
        self.estimate.setObjectName("water_estimate")
        self.estimate.clicked.connect(lambda: self.prepare(estimate_only=True))
        self.estimate.setVisible(kind == "count")
        layout.addWidget(self.estimate)
        self.message = QLabel("请先预览；预览中的水就是最终添加的水。")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.setCenterButtons(True)
        self.apply = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.apply.setText("添加")
        self.apply.setEnabled(False)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.range_changed()

    def region(self):
        return Region.from_structure(self.structure, self.lower.value(), self.upper.value())

    def range_changed(self, _value=None):
        self.free = None
        try:
            region = self.region()
        except ValueError as error:
            self.summary.setText(str(error))
            self.preview.setEnabled(False)
            self.boundary.setEnabled(False)
            self.estimate.setEnabled(False)
            self.changed()
            return
        self.preview.setEnabled(True)
        self.boundary.setEnabled(True)
        self.estimate.setEnabled(True)
        if self.kind == "layer":
            for spin, number in zip(self.counts, region.recommended_grid):
                spin.blockSignals(True)
                spin.setRange(1, max(spin.value(), number + 1))
                if self.initial:
                    spin.setValue(max(1, number))
                spin.blockSignals(False)
        elif self.initial:
            self.count.setValue(max(1, min(5000, int(region.volume / MASS_VOLUME))))
        self.initial = False
        self.changed()

    def changed(self, _value=None):
        self.prepared = None
        self.invalidated.emit()
        self.apply.setEnabled(False)
        self.message.setText("参数已改变，请先预览。")
        try:
            region = self.region()
        except ValueError:
            return
        count = self.quantity()
        text = f"范围体积 {region.volume:.2f} Å³；{count} 个水；范围密度 {density(count, region.volume):.2f} g/cm³"
        if self.kind == "layer":
            recommended = " × ".join(map(str, region.recommended_grid))
            maximum = " × ".join(str(spin.maximum()) for spin in self.counts)
            text += f"\n推荐 x × y × z：{recommended}；允许上限：{maximum}"
        if self.free is not None:
            volume, resolution = self.free
            text += f"\n剩余体积约 {volume:.2f} Å³（采样量级 {resolution:.2f} Å³）；推荐 {int(volume / MASS_VOLUME)} 个水"
            text += f"\n按剩余体积估算水密度 {density(count, volume):.2f} g/cm³"
        elif self.kind == "count":
            text += "\n点击预览估算实际剩余体积及推荐数量。"
        self.summary.setText(text)

    def quantity(self):
        if self.kind == "count":
            return self.count.value()
        return self.counts[0].value() * self.counts[1].value() * self.counts[2].value()

    def show_range(self):
        self.range_requested.emit(self.region())

    def prepare(self, _checked=False, *, estimate_only=False):
        self.prepared = None
        self.apply.setEnabled(False)
        region = self.region()
        parameters = dict(seed=self.seed.value(), factor=self.factor.value())
        parameters["grid" if self.kind == "layer" else "count"] = tuple(spin.value() for spin in self.counts) if self.kind == "layer" else self.count.value()
        self.worker = WaterWorker(self.structure, region, None if estimate_only else parameters, self)
        self.worker.estimated.connect(lambda result: self.estimated(result, estimate_only))
        self.worker.prepared.connect(self.ready)
        self.worker.failed.connect(self.failed)
        self.worker.progress.connect(lambda value: self.message.setText(f"正在生成：{value}/{self.quantity()} 个水分子…"))
        self.worker.finished.connect(self.worker_done)
        self.form.setEnabled(False)
        self.preview.setEnabled(False)
        self.boundary.setEnabled(False)
        self.estimate.setEnabled(False)
        self.message.setText("正在估算剩余体积并生成水分子…")
        self.show_range()
        self.worker.start()

    def estimated(self, result, estimate_only):
        self.free = result
        if estimate_only and self.kind == "count":
            self.count.setValue(max(1, min(5000, int(result[0] / MASS_VOLUME))))
        self.changed()
        if estimate_only:
            self.message.setText("剩余体积已估算；可调整水数量，再点击预览水分子。")

    def ready(self, result):
        group, volume, resolution = result
        self.free = (volume, resolution)
        self.changed()
        self.prepared = group
        preview = deepcopy(self.structure)
        indices = preview.add_group(*group)
        self.preview_requested.emit(preview, indices)
        self.message.setText("预览完成；可以旋转、平移视角，点击添加确认。")

    def failed(self, message):
        self.message.setText(message)

    def worker_done(self):
        worker, self.worker = self.worker, None
        worker.deleteLater()
        self.form.setEnabled(True)
        self.preview.setEnabled(True)
        self.boundary.setEnabled(True)
        self.estimate.setEnabled(True)
        self.apply.setEnabled(self.prepared is not None and not self.closing)
        if self.closing:
            super().reject()

    def reject(self):
        if self.worker is not None and self.worker.isRunning():
            self.closing = True
            self.worker.requestInterruption()
            self.message.setText("正在停止生成…")
        else:
            super().reject()
