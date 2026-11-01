"""Compact optimization controls and per-step statistics in viewport overlays."""

from pathlib import Path
import re

from PySide6.QtCore import QSignalBlocker, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPen
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                              QHBoxLayout, QLabel, QLineEdit, QPushButton, QRadioButton,
                              QScrollArea, QSpinBox, QStyle, QToolButton, QVBoxLayout, QWidget)
from .overlay import Overlay
from .walls import Wall, Walls
from .mlip import MODELS
from .devices import gpu_device, has_gpu_backend


def hardware_label(name):
    name = name.replace("NVIDIA GeForce ", "").replace("NVIDIA ", "")
    match = re.search(r"(?:i[3579]-[\w]+|Ultra\s+\d\s+[\w]+|Ryzen\s+[\w\s-]+)", name)
    return match.group(0).strip() if match else name


class LoadStatus(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(24, 24)
        self.angle, self.loading, self.ready = 0, False, False
        self.timer = QTimer(self)
        self.timer.setInterval(60)
        self.timer.timeout.connect(self.advance)

    def advance(self):
        self.angle = (self.angle + 25) % 360
        self.update()

    def set_state(self, loading, ready):
        self.loading, self.ready = loading, ready
        self.timer.start() if loading else self.timer.stop()
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.loading:
            painter.setPen(QPen(QColor("#3982b8"), 2))
            painter.drawArc(4, 4, 16, 16, self.angle * 16, 270 * 16)
        elif self.ready:
            painter.setPen(QPen(QColor("#22944a"), 3))
            painter.drawLine(4, 12, 10, 18)
            painter.drawLine(10, 18, 21, 5)


class RelaxPanel(Overlay):
    start_requested = Signal()
    path_changed = Signal(str)
    export_requested = Signal(str)
    view_requested = Signal(str)
    walls_changed = Signal()

    def __init__(self, controller, parent):
        super().__init__(parent)
        self.controller, self.state = controller, None
        self.fix_shortcut = 'Ctrl+F'
        self.setObjectName("relax_panel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoMousePropagation, True)
        self.setStyleSheet("#relax_panel { background: #f3f3f3; border: 1px solid #adb3ba; border-radius: 4px; }")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 5, 8, 5)
        outer.setSpacing(3)
        outer.addWidget(self.grip)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body = QWidget()
        scroll.setWidget(self.body)
        outer.addWidget(scroll)
        layout = QVBoxLayout(self.body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.model = QComboBox()
        for name, model in MODELS.items():
            self.model.addItem(model.label, name)
            self.model.setItemData(self.model.count() - 1, model.tooltip, Qt.ItemDataRole.ToolTipRole)
        self.model.setCurrentIndex(self.model.findData(controller.model))
        self.model.currentIndexChanged.connect(self.select_model)
        layout.addWidget(self.model)
        row = QHBoxLayout()
        self.path = QLineEdit(str(controller.path))
        self.path.setObjectName("model_path")
        self.path.setReadOnly(True)
        self.path.setToolTip(str(controller.path))
        row.addWidget(self.path, 1)
        self.browse = QPushButton("选择")
        self.browse.clicked.connect(self.select_path)
        row.addWidget(self.browse)
        self.folder = QToolButton()
        self.folder.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        self.folder.setToolTip("打开模型目录")
        self.folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(controller.path.parent))))
        row.addWidget(self.folder)
        layout.addLayout(row)
        self.gpu, self.cpu = QRadioButton("GPU · 检测中"), QRadioButton("CPU · 检测中")
        self.gpu.setChecked(controller.device == gpu_device())
        self.cpu.setChecked(controller.device == "cpu")
        self.gpu.toggled.connect(self.select_device)
        layout.addWidget(self.gpu)
        layout.addWidget(self.cpu)
        row = QHBoxLayout()
        self.load = QPushButton("加载模型")
        self.load.setObjectName("load_model")
        self.load.clicked.connect(controller.load)
        row.addWidget(self.load)
        self.indicator = LoadStatus()
        row.addWidget(self.indicator)
        layout.addLayout(row)
        self.model_status = QLabel()
        self.model_status.setWordWrap(True)
        layout.addWidget(self.model_status)
        form = QFormLayout()
        self.force = QDoubleSpinBox()
        self.force.setObjectName("relax_fmax")
        self.force.setRange(0.01, 100)
        self.force.setDecimals(2)
        self.force.setSingleStep(0.01)
        self.force.setValue(0.05)
        self.force.setSuffix(" eV/Å")
        self.force.valueChanged.connect(self.parameters)
        self.force_label = QLabel("最大力")
        form.addRow(self.force_label, self.force)
        self.steps = QSpinBox()
        self.steps.setObjectName("relax_steps")
        self.steps.setRange(1, 100000)
        self.steps.setValue(200)
        self.steps.valueChanged.connect(self.parameters)
        self.steps_label = QLabel("最大步数")
        form.addRow(self.steps_label, self.steps)
        self.temperature = QDoubleSpinBox()
        self.temperature.setRange(0, 10000)
        self.temperature.setDecimals(2)
        self.temperature.setSuffix(" K")
        self.temperature.valueChanged.connect(self.parameters)
        self.temperature_label = QLabel("温度")
        form.addRow(self.temperature_label, self.temperature)
        self.timestep = QDoubleSpinBox()
        self.timestep.setRange(0.01, 100)
        self.timestep.setDecimals(2)
        self.timestep.setSuffix(" fs")
        self.timestep.valueChanged.connect(self.parameters)
        self.timestep_label = QLabel("步长")
        form.addRow(self.timestep_label, self.timestep)
        layout.addLayout(form)
        self.walls = {}
        for name, label, initial in (("upper", "开启上墙", 20), ("lower", "开启下墙", 0)):
            enabled = QCheckBox(label)
            enabled.toggled.connect(self.parameters)
            layout.addWidget(enabled)
            fields = QWidget()
            wall_form = QFormLayout(fields)
            wall_form.setContentsMargins(0, 0, 0, 0)
            position, spring = QDoubleSpinBox(), QDoubleSpinBox()
            position.setRange(-100000, 100000)
            position.setDecimals(2)
            position.setValue(initial)
            position.setSuffix(" Å")
            spring.setRange(0.01, 10000)
            spring.setDecimals(2)
            spring.setValue(4)
            spring.setSuffix(" eV/Å²")
            for widget in (position, spring):
                widget.valueChanged.connect(self.parameters)
            wall_form.addRow("z 位置", position)
            wall_form.addRow("弹簧系数", spring)
            layout.addWidget(fields)
            self.walls[name] = (enabled, fields, position, spring)
        self.preview_walls = QPushButton("预览墙位置")
        self.preview_walls.setCheckable(True)
        self.preview_walls.clicked.connect(lambda: self.view_requested.emit("walls"))
        layout.addWidget(self.preview_walls)
        self.guide = QLabel()
        self.guide.setWordWrap(True)
        layout.addWidget(self.guide)
        row = QHBoxLayout()
        self.start = QPushButton("开始优化")
        self.start.setObjectName("start_relax")
        self.start.clicked.connect(self.start_requested)
        row.addWidget(self.start)
        self.stop = QPushButton("停止")
        self.stop.clicked.connect(controller.stop)
        row.addWidget(self.stop)
        layout.addLayout(row)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.exports = {}
        for names in (("trajectory", "final"), ("data", "table"), ("curves", "info")):
            row = QHBoxLayout()
            for name in names:
                label = {"trajectory": "导出轨迹", "final": "导出最终结构", "data": "导出数据",
                         "table": "查看数据", "curves": "查看曲线", "info": "显示过程"}[name]
                button = QPushButton(label)
                if name in ("table", "curves", "info"):
                    button.setCheckable(True)
                    button.clicked.connect(lambda _checked, key=name: self.view_requested.emit(key))
                else:
                    button.clicked.connect(lambda _checked, key=name: self.export_requested.emit(key))
                self.exports[name] = button
                row.addWidget(button)
            layout.addLayout(row)
        self.setFixedWidth(250)
        self.hide()

    def select_path(self):
        model = MODELS[self.controller.model]
        path, _filter = QFileDialog.getOpenFileName(self, "选择 " + model.name + " 模型", str(self.controller.path.parent),
                                                   model.file_filter)
        if path:
            self.controller.select(Path(path), self.controller.device)
            self.path_changed.emit(path)

    def select_device(self, gpu):
        self.controller.select(self.controller.path, gpu_device() if gpu else "cpu")

    def select_model(self, _index):
        name = self.model.currentData()
        self.controller.select(self.controller.paths[name], self.controller.device, name)
        self.path_changed.emit(str(self.controller.path))

    def parameters(self, *_args):
        if self.state is not None:
            self.state.fmax, self.state.max_steps = self.force.value(), self.steps.value()
            self.state.temperature, self.state.timestep = self.temperature.value(), self.timestep.value()
            self.state.walls = Walls(**{name: Wall(box.isChecked(), position.value(), spring.value())
                                       for name, (box, _fields, position, spring) in self.walls.items()})
            self.show_wall_fields()
            self.walls_changed.emit()

    def show_wall_fields(self):
        md = self.state is not None and self.state.kind == "md"
        for enabled, fields, _position, _spring in self.walls.values():
            enabled.setVisible(md)
            fields.setVisible(md and enabled.isChecked())
        self.preview_walls.setVisible(md)

    def update_guide(self):
        key = self.fix_shortcut
        self.guide.setText('' if self.state is not None and self.state.kind == 'md' else
                          '临时全固定；' + (key + ' 解除所选固定。' if key else '在编辑菜单解除所选固定。')
                          + '结构导出使用原始约束。')

    def fit(self, bottom):
        height = self.body.sizeHint().height() + self.grip.height() + 25
        self.resize(self.width(), min(height, max(120, bottom - self.y() - 8)))

    def populate(self, state, editable):
        self.state = state
        self.setVisible(state is not None)
        if state is None:
            self.indicator.set_state(False, False)
            return
        controller = self.controller
        md = state.kind == "md"
        self.grip.setText("MLIP 预平衡" if md else "MLIP 结构优化")
        for widget in (self.force, self.force_label):
            widget.setVisible(not md)
        for widget in (self.temperature, self.temperature_label, self.timestep, self.timestep_label):
            widget.setVisible(md)
        self.steps_label.setText("步数" if md else "最大步数")
        self.steps.setSingleStep(1000 if md else 1)
        self.start.setText("开始预平衡" if md else "开始优化")
        self.update_guide()
        self.guide.setVisible(not md)
        with QSignalBlocker(self.force), QSignalBlocker(self.steps), QSignalBlocker(self.temperature), QSignalBlocker(self.timestep):
            self.force.setValue(state.fmax)
            self.steps.setValue(state.max_steps)
            self.temperature.setValue(state.temperature)
            self.timestep.setValue(state.timestep)
        for name, (box, _fields, position, spring) in self.walls.items():
            wall = getattr(state.walls, name)
            with QSignalBlocker(box), QSignalBlocker(position), QSignalBlocker(spring):
                box.setChecked(wall.enabled)
                position.setValue(wall.position)
                spring.setValue(wall.spring)
            for widget in (box, position, spring):
                widget.setEnabled(not state.running)
        self.show_wall_fields()
        self.path.setText(str(controller.path))
        with QSignalBlocker(self.model):
            self.model.setCurrentIndex(self.model.findData(controller.model))
        self.path.setToolTip(str(controller.path))
        self.gpu.setText("GPU · " + hardware_label(controller.gpu))
        self.cpu.setText("CPU · " + hardware_label(controller.cpu))
        self.gpu.setToolTip(controller.gpu)
        self.cpu.setToolTip(controller.cpu)
        with QSignalBlocker(self.gpu), QSignalBlocker(self.cpu):
            self.gpu.setChecked(controller.device == gpu_device())
            self.cpu.setChecked(controller.device == "cpu")
        available = controller.gpu not in ("Fail", "检测中", "不支持") if controller.device != "cpu" else controller.hardware is None
        self.load.setEnabled(not controller.busy and available and controller.path.is_file())
        for widget in (self.model, self.browse, self.gpu, self.cpu):
            widget.setEnabled(not controller.busy)
        self.gpu.setEnabled(not controller.busy and has_gpu_backend(MODELS[controller.model].engine))
        for widget in (self.force, self.steps, self.temperature, self.timestep):
            widget.setEnabled(not state.running)
        self.indicator.set_state(controller.loader is not None, controller.calculator is not None)
        self.model_status.setText(controller.message)
        self.start.setEnabled(bool(editable and not controller.busy and controller.calculator is not None))
        self.stop.setEnabled(state.running)
        self.status.setText(state.status)
        self.fit(self.parentWidget().height())


class RelaxInfo(Overlay):
    close_requested = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("relax_info")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoMousePropagation, True)
        self.setStyleSheet("#relax_info { background: #f3f3f3; border: 1px solid #adb3ba; border-radius: 4px; }")
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        header.addWidget(self.grip, 1)
        self.close_button = QToolButton()
        self.close_button.setText("×")
        self.close_button.setToolTip("关闭过程窗口")
        self.close_button.clicked.connect(self.close_requested)
        header.addWidget(self.close_button)
        layout.addLayout(header)
        self.form = form = QFormLayout()
        layout.addLayout(form)
        self.bottom = parent.height()
        self.values = {}
        for key, label in (("step", "步数"), ("energy", "能量 (eV)"), ("per_atom", "每原子能量 (eV)"),
                           ("temperature", "温度 (K)"), ("time", "时间 (fs)"),
                           ("speed", "步/秒"), ("elapsed", "用时 (秒)"),
                           ("force", "原子最大受力 (eV/Å)")):
            value = QLabel("—")
            value.setMinimumWidth(65)
            self.values[key] = value
            form.addRow(label, value)
        self.hide()

    def populate(self, state, count, visible=True):
        self.setVisible(state is not None and visible)
        if state is None:
            return
        step = state.frames[-1]
        self.grip.setText("预平衡过程" if state.kind == "md" else "优化过程")
        form = self.form
        for key in ("temperature", "time"):
            self.values[key].setVisible(state.kind == "md")
            form.labelForField(self.values[key]).setVisible(state.kind == "md")
        self.values["step"].setText(str(step.number))
        self.values["energy"].setText("—" if step.energy is None else f"{step.energy:.3f}")
        self.values["per_atom"].setText("—" if step.energy is None else f"{step.energy / count:.3f}")
        self.values["force"].setText("—" if step.force is None else f"{step.force:.3f}")
        self.values["temperature"].setText("—" if step.temperature is None else f"{step.temperature:.2f}")
        self.values["time"].setText(f"{step.time_fs:.2f}")
        self.values["speed"].setText(f"{step.number / step.elapsed:.2f}" if step.elapsed else "—")
        self.values["elapsed"].setText(f"{step.elapsed:.2f}")
        self.adjustSize()

    def keep_visible(self):
        super().keep_visible()
        if self.isVisible():
            self.move(max(0, min(self.x(), self.parentWidget().width() - self.width() - 8)),
                      max(0, min(self.y(), self.bottom - self.height() - 8)))
