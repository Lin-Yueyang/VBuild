"""Movable MLIP force settings; opening the panel never loads or enables a model."""

from pathlib import Path

import numpy as np
from PySide6.QtCore import QSignalBlocker, QUrl, Signal, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                              QHBoxLayout, QLabel, QLineEdit, QPushButton,
                              QRadioButton, QStyle, QToolButton, QVBoxLayout)

from .mlip import MODELS, available_models
from .devices import gpu_device, has_gpu_backend
from .overlay import Overlay
from .relax_panel import ChargeSpin, LoadStatus, hardware_label


class ForcePanel(Overlay):
    preferences_changed = Signal()

    def __init__(self, controller, parent):
        super().__init__(parent)
        self.controller = controller
        self.setObjectName("force_panel")
        self.setStyleSheet("#force_panel { background: #ffffff; border: 1px solid #bbc3cc; border-radius: 4px; }")
        self.setFixedWidth(250)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(5)
        header = QHBoxLayout()
        self.grip.setText("原子受力")
        header.addWidget(self.grip, 1)
        close = QToolButton()
        close.setText("×")
        close.setToolTip("关闭设置框；受力开关由下方按钮控制")
        close.clicked.connect(self.hide)
        header.addWidget(close)
        layout.addLayout(header)
        self.model = QComboBox()
        for name, model in MODELS.items():
            self.model.addItem(model.label, name)
            self.model.setItemData(self.model.count() - 1, model.tooltip, Qt.ItemDataRole.ToolTipRole)
        self.model.setCurrentIndex(self.model.findData(controller.model))
        self.model.currentIndexChanged.connect(self.choose_model)
        layout.addWidget(self.model)
        self.charge_spin = ChargeSpin(controller)
        layout.addWidget(self.charge_spin)
        row = QHBoxLayout()
        self.path = QLineEdit()
        self.path.setReadOnly(True)
        row.addWidget(self.path, 1)
        self.browse = QToolButton()
        self.browse.setText("…")
        self.browse.setToolTip("选择本地模型文件")
        self.browse.clicked.connect(self.choose_path)
        row.addWidget(self.browse)
        self.folder = QToolButton()
        self.folder.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        self.folder.setToolTip("打开模型目录")
        self.folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(controller.path.parent))))
        row.addWidget(self.folder)
        layout.addLayout(row)
        self.gpu, self.cpu = QRadioButton(), QRadioButton()
        self.gpu.toggled.connect(lambda active: self.choose_device(gpu_device()) if active else None)
        self.cpu.toggled.connect(lambda active: self.choose_device("cpu") if active else None)
        layout.addWidget(self.gpu)
        layout.addWidget(self.cpu)
        row = QHBoxLayout()
        self.load = QPushButton("加载模型")
        self.load.clicked.connect(controller.load)
        row.addWidget(self.load, 1)
        self.indicator = LoadStatus()
        row.addWidget(self.indicator)
        layout.addLayout(row)
        form = QFormLayout()
        self.threshold = QDoubleSpinBox()
        self.threshold.setObjectName("force_threshold")
        self.threshold.setRange(0, 10000)
        self.threshold.setDecimals(2)
        self.threshold.setSingleStep(.1)
        self.threshold.setValue(controller.threshold)
        self.threshold.setSuffix(" eV/Å")
        form.addRow("显示阈值", self.threshold)
        self.scale = QDoubleSpinBox()
        self.scale.setObjectName("force_scale")
        self.scale.setRange(.01, 100)
        self.scale.setDecimals(2)
        self.scale.setSingleStep(.1)
        self.scale.setValue(controller.scale)
        self.scale.setToolTip("值为 1 时，1 eV/Å 的力对应 1 Å 长箭头。")
        form.addRow("箭头比例", self.scale)
        self.threshold.valueChanged.connect(self.parameters)
        self.scale.valueChanged.connect(self.parameters)
        layout.addLayout(form)
        self.toggle = QPushButton("启用")
        self.toggle.setObjectName("enable_forces")
        self.toggle.clicked.connect(lambda: controller.enable(not controller.enabled))
        layout.addWidget(self.toggle)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        note = QLabel("显示原始模型力，包含固定原子。")
        note.setToolTip("不会修改原子坐标或约束。箭头方向为力方向，标注为力大小。")
        layout.addWidget(note)
        self.hide()
        self.sync()

    def choose_model(self, _index):
        name = self.model.currentData()
        self.controller.select(self.controller.paths[name], self.controller.device, name)
        self.preferences_changed.emit()

    def choose_path(self):
        model = MODELS[self.controller.model]
        path, _filter = QFileDialog.getOpenFileName(self, "选择 " + model.name,
                                                   str(self.controller.path.parent), model.file_filter)
        if path:
            self.controller.select(Path(path), self.controller.device)
            self.preferences_changed.emit()

    def choose_device(self, device):
        self.controller.select(self.controller.path, device)
        self.preferences_changed.emit()

    def parameters(self, *_args):
        self.controller.threshold, self.controller.scale = self.threshold.value(), self.scale.value()
        self.controller.changed.emit()
        self.preferences_changed.emit()

    def sync(self):
        controller = self.controller
        available_models(self.model, controller.paths)
        self.charge_spin.sync()
        with QSignalBlocker(self.model), QSignalBlocker(self.gpu), QSignalBlocker(self.cpu):
            self.model.setCurrentIndex(self.model.findData(controller.model))
            self.gpu.setChecked(controller.device == gpu_device())
            self.cpu.setChecked(controller.device == "cpu")
        self.path.setText(str(controller.path))
        self.path.setToolTip(str(controller.path))
        self.gpu.setText("GPU · " + hardware_label(controller.gpu))
        self.cpu.setText("CPU · " + hardware_label(controller.cpu))
        for widget in (self.model, self.browse, self.gpu, self.cpu):
            widget.setEnabled(not controller.busy)
        self.gpu.setEnabled(not controller.busy and has_gpu_backend(MODELS[controller.model].engine))
        available = controller.gpu not in ("检测中", "Fail", "不支持") if controller.device != "cpu" else controller.hardware is None
        self.load.setEnabled(not controller.busy and available and controller.path.is_file())
        self.indicator.set_state(controller.loader is not None, controller.calculator is not None)
        self.toggle.setEnabled(controller.enabled or controller.calculator is not None)
        self.toggle.setText("关闭" if controller.enabled else "启用")
        text = "正在计算当前结构…" if controller.task is not None else controller.message
        if controller.enabled and controller.values is not None and controller.target is not None and controller.result_key == controller.target[0]:
            count = int((np.linalg.norm(controller.values, axis=1) > controller.threshold).sum())
            text += f"\n超过阈值：{count} / {len(controller.values)} 个原子"
        self.status.setText(text)
        self.adjustSize()
