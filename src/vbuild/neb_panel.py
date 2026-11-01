"""Movable NEB controls and current/all-step data inside the page's analysis tabs."""

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel, QListWidget,
                              QListWidgetItem, QPushButton, QScrollArea, QTableView, QVBoxLayout, QWidget)

from .neb import ENERGIES
from .overlay import Overlay


class ImageButtons(QWidget):
    changed = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.run, self.buttons = None, {}
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(4)

    def populate(self, run):
        if run is not self.run:
            self.run = run
            while self.row.count():
                self.row.takeAt(0).widget().deleteLater()
            self.buttons = {}
            for index, image in enumerate(run.images):
                button = QPushButton(image.name)
                button.setCheckable(True)
                button.setMinimumWidth(30)
                button.setToolTip("展示图像 " + image.name + " 的数据；不改变结构叠加")
                button.toggled.connect(lambda checked, key=index: self.select(key, checked))
                self.row.addWidget(button)
                self.buttons[index] = button
        for index, button in self.buttons.items():
            with QSignalBlocker(button):
                button.setChecked(index in run.analysis_selected)

    def select(self, index, checked):
        self.run.analysis_selected.add(index) if checked else self.run.analysis_selected.discard(index)
        self.changed.emit()


class NebPanel(Overlay):
    changed = Signal()
    pane_requested = Signal(str, bool)
    refresh_requested = Signal()

    def __init__(self, parent):
        super().__init__(parent, resizable=True)
        self.run = None
        self.setObjectName("neb_panel")
        self.setStyleSheet("#neb_panel { background: #f6f8fb; border: 1px solid #adb3ba; }")
        self.resize(255, 440)
        self.setMinimumWidth(215)
        outer = QVBoxLayout(self)
        self.grip.setText("NEB · 图像叠加")
        outer.addWidget(self.grip)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        self.caption = QLabel()
        self.caption.setWordWrap(True)
        layout.addWidget(self.caption)
        row = QHBoxLayout()
        all_button, none_button = QPushButton("全部显示"), QPushButton("全部隐藏")
        all_button.clicked.connect(lambda: self.select_all(True))
        none_button.clicked.connect(lambda: self.select_all(False))
        row.addWidget(all_button)
        row.addWidget(none_button)
        layout.addLayout(row)
        self.images = QListWidget()
        self.images.setMinimumHeight(90)
        self.images.setMaximumHeight(210)
        self.images.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.checks, self.opaque_checks = {}, {}
        layout.addWidget(self.images, 1)
        for key, label, default in (("opacity", "原子不透明度", .30), ("bond_opacity", "键不透明度", .18)):
            field = QDoubleSpinBox()
            field.setRange(.02, 1)
            field.setDecimals(2)
            field.setSingleStep(.05)
            field.setValue(default)
            field.setToolTip("数值越小越透明；不影响勾选为不透明的图像")
            field.valueChanged.connect(lambda number, name=key: self.opacity_changed(name, number))
            setattr(self, key, field)
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            row.addWidget(field)
            layout.addLayout(row)
        self.energy = QComboBox()
        for name, label in ENERGIES:
            self.energy.addItem(label, name)
        self.energy.currentIndexChanged.connect(self.energy_changed)
        row = QHBoxLayout()
        row.addWidget(QLabel("能量口径"))
        row.addWidget(self.energy)
        layout.addLayout(row)
        self.buttons = {}
        row = QHBoxLayout()
        for name, label in (("neb_data", "查看数据"), ("neb_profile", "能量路径图"), ("neb_history", "能量 / 受力曲线")):
            button = QPushButton(label)
            button.setCheckable(True)
            button.toggled.connect(lambda checked, key=name: self.pane_requested.emit(key, checked))
            self.buttons[name] = button
            (layout if name == "neb_history" else row).addWidget(button)
        layout.insertLayout(layout.count()-1, row)
        refresh = QPushButton("刷新数据（重新扫描确认）")
        refresh.clicked.connect(self.refresh_requested)
        layout.addWidget(refresh)
        self.notes = QLabel()
        self.notes.setWordWrap(True)
        layout.addWidget(self.notes)
        layout.addStretch()
        self.hide()

    def populate(self, run, frame):
        if run is not self.run:
            self.run = run
            self.images.clear()
            self.checks, self.opaque_checks = {}, {}
            for index, image in enumerate(run.images):
                item, widget = QListWidgetItem(), QWidget()
                row = QHBoxLayout(widget)
                row.setContentsMargins(3, 2, 3, 2)
                visible, opaque = QCheckBox(image.name + f" · {image.count} 帧"), QCheckBox("不透明")
                visible.toggled.connect(lambda checked, key=index: self.select_image(key, checked))
                opaque.toggled.connect(lambda checked, key=index: self.set_opaque(key, checked))
                row.addWidget(visible)
                row.addStretch()
                row.addWidget(opaque)
                item.setSizeHint(widget.sizeHint())
                item.setToolTip(str(image.path) + ("\n" + image.notice if image.notice else ""))
                self.images.addItem(item)
                self.images.setItemWidget(item, widget)
                self.checks[index], self.opaque_checks[index] = visible, opaque
        for index in self.checks:
            with QSignalBlocker(self.checks[index]), QSignalBlocker(self.opaque_checks[index]):
                self.checks[index].setChecked(index in run.selected)
                self.opaque_checks[index].setChecked(index in run.opaque)
        with QSignalBlocker(self.opacity), QSignalBlocker(self.bond_opacity), QSignalBlocker(self.energy):
            self.opacity.setValue(run.opacity)
            self.bond_opacity.setValue(run.bond_opacity)
            self.energy.setCurrentIndex(self.energy.findData(run.energy_key))
        for key, field in (("neb_data", "show_data"), ("neb_history", "show_history"), ("neb_profile", "show_profile")):
            with QSignalBlocker(self.buttons[key]):
                self.buttons[key].setChecked(getattr(run, field))
        self.caption.setText(f"同步帧 {frame+1} / {len(run.offsets)}")
        self.notes.setText("静态端点保持不动；较短记录保持末帧。")

    def select_all(self, visible):
        self.run.selected = set(range(self.images.count())) if visible else set()
        self.changed.emit()

    def select_image(self, index, checked):
        self.run.selected.add(index) if checked else self.run.selected.discard(index)
        self.changed.emit()

    def opacity_changed(self, name, number):
        setattr(self.run, name, number)
        self.changed.emit()

    def set_opaque(self, index, checked):
        self.run.opaque.add(index) if checked else self.run.opaque.discard(index)
        self.changed.emit()

    def energy_changed(self, _index):
        self.run.energy_key = self.energy.currentData()
        self.changed.emit()

    def fit(self, bottom):
        self.place(12, 12)
        self.move(self.x(), max(0, min(self.y(), bottom-190)))
        self.resize(self.width(), max(0, min(660, bottom-self.y()-8)))


class NebTable(QAbstractTableModel):
    TITLES = ("收敛", "图像", "帧", "总帧数", "能量 (eV)", "ΔE (eV)", "原子最大受力 (eV/Å)", "状态")

    def __init__(self, parent):
        super().__init__(parent)
        self.run, self.frame, self.all_frames, self.rows = None, 0, False, []
        self.chosen = ()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.TITLES)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole:
            return self.TITLES[section] if orientation == Qt.Orientation.Horizontal else str(section+1)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return Qt.AlignmentFlag.AlignCenter
        if role == Qt.ItemDataRole.ToolTipRole and index.column() in (0, 6):
            return "收敛按 INCAR 的 EDIFFG 判断；受力取 OUTCAR 的 FORCES: max atom, RMS。缺少对应数据为 -。"
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        image_index, frame = self.rows[index.row()]
        image = self.run.images[image_index]
        actual = min(frame, image.count-1)
        record = image.records[actual]
        energy = record.get(self.run.energy_key)
        reference_index, energies = self.run.energy_reference(frame)
        reference = None if reference_index is None else energies[reference_index]
        relative = None if energy is None or reference is None else energy-reference
        state = "静态" if image.count == 1 and image.reader is None else "保持末帧" if frame >= image.count else "动态"
        row = (self.run.convergence(image_index, actual), image.name, actual+1, image.count, energy, relative, record.get("max_force"), state)
        value = row[index.column()]
        return "—" if value is None else f"{value:.6f}" if isinstance(value, float) else str(value)

    def populate(self, run, frame, all_frames):
        chosen = tuple(sorted(run.analysis_selected))
        if run is not self.run or all_frames != self.all_frames or chosen != self.chosen:
            self.beginResetModel()
            self.run, self.frame, self.all_frames = run, frame, all_frames
            self.chosen = chosen
            self.rows = ([(image, step) for image in chosen for step in range(run.images[image].count)]
                         if all_frames else [(image, frame) for image in chosen])
            self.endResetModel()
        else:
            self.frame = frame
            if not all_frames:
                self.rows = [(image, frame) for image in chosen]
            if self.rows:
                self.dataChanged.emit(self.index(0, 0), self.index(len(self.rows)-1, len(self.TITLES)-1))


class NebDataPanel(QWidget):
    changed = Signal()
    export_requested = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.images = ImageButtons(self)
        self.images.changed.connect(self.changed)
        layout.addWidget(self.images)
        self.caption = QLabel()
        self.caption.setWordWrap(True)
        layout.addWidget(self.caption)
        row = QHBoxLayout()
        self.all_frames = QCheckBox("列出全部已有优化帧")
        self.all_frames.toggled.connect(self.refresh)
        row.addWidget(self.all_frames)
        export = QPushButton("导出数据")
        export.setToolTip("导出所选图像的全部实际计算记录")
        export.clicked.connect(self.export_requested)
        row.addWidget(export)
        layout.addLayout(row)
        self.table = QTableView()
        self.model = NebTable(self.table)
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionsMovable(True)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table, 1)
        self.hide()

    def populate(self, run, frame):
        self.images.populate(run)
        first = self.model.run is not run
        self.model.populate(run, frame, self.all_frames.isChecked())
        reference, _energies = run.energy_reference(frame)
        name = "—" if reference is None else run.images[reference].name
        self.caption.setText(f"同步帧 {frame+1} · {run.energy_key} · ΔE 参考图像 {name}")
        if first:
            self.table.resizeColumnsToContents()
            width = self.table.fontMetrics().horizontalAdvance(str(len(run.offsets)))+24
            self.table.setColumnWidth(2, max(width, self.table.columnWidth(2)))

    def refresh(self):
        if self.model.run is not None:
            self.populate(self.model.run, self.model.frame)
