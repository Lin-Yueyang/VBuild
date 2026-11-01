"""Movable electron-grid controls; the two sidebar actions belong to the active page."""

import numpy as np
from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
                              QLabel, QPushButton, QToolButton, QVBoxLayout)

from .overlay import Overlay


class Number(QDoubleSpinBox):
    def __init__(self):
        super().__init__()
        self.setRange(-1e10, 1e10)
        self.setDecimals(8)

    def textFromValue(self, value):
        return f"{value:.8f}".rstrip("0").rstrip(".") or "0"


def defaults(volume):
    data = volume.channels[0]
    low, high = float(data.min()), float(data.max())
    signed = low < 0 < high
    level = .75 if volume.family == "elf" else .05
    if signed:
        level = min(level, max(-low, high) / 2)
    if not signed and not low < level < high:
        level = (low + high) / 2
    if high == low:
        high += max(abs(low) * .01, 1e-6)
    return {"enabled": False, "source": 0, "channel": 0, "mode": "slice" if volume.family == "elf" else "iso",
            "level": level, "negative_level": -abs(level), "level_lock": True,
            "axis": 5 if volume.family == "elf" else 2,
            "position": float(volume.document.structure.cell[:, 2].sum()/2) if volume.family == "elf" else .5,
            "opacity": .6 if volume.family == "elf" else .35,
            "color": "#e5ad24" if signed else "#3697ed", "negative_color": "#3697ed",
            "positive": True, "negative": signed, "signed": signed,
            "palette": "turbo", "low": 0 if volume.family == "elf" else low,
            "high": 1 if volume.family == "elf" else high}


class VolumePanel(Overlay):
    changed = Signal()
    slice_requested = Signal(bool)

    def __init__(self, family, parent):
        super().__init__(parent)
        self.family, self.volumes, self.options = family, [], None
        self.setObjectName("volume_" + family)
        self.setStyleSheet(f"#volume_{family} {{ background: white; border: 1px solid #bbc3cc; border-radius: 4px; }}")
        self.setFixedWidth(270)
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        self.grip.setText("ELFCAR" if family == "elf" else "CHGCAR / 电子数据")
        header.addWidget(self.grip, 1)
        close = QToolButton()
        close.setText("×")
        close.clicked.connect(self.hide)
        header.addWidget(close)
        layout.addLayout(header)
        self.form = QFormLayout()
        layout.addLayout(self.form)
        self.source, self.channel, self.mode, self.axis, self.palette = (QComboBox() for _index in range(5))
        self.mode.addItem("等值面", "iso")
        self.mode.addItem("切片", "slice")
        self.axis.addItems(["平行 bc（a 分数）", "平行 ac（b 分数）", "平行 ab（c 分数）", "笛卡尔 x", "笛卡尔 y", "笛卡尔 z"])
        self.palette.addItems(["turbo", "viridis", "coolwarm", "gray"])
        self.level, self.position, self.low, self.high = (Number() for _index in range(4))
        self.negative_level = Number()
        self.level_lock = QPushButton("锁定正负一致")
        self.level_lock.setCheckable(True)
        self.level_lock.setToolTip("按下时两侧的绝对值一致；解除后可分别调整正、负等值")
        self.level_lock.toggled.connect(self.lock_levels)
        self.set_level_step(.01)
        self.opacity = QDoubleSpinBox()
        self.opacity.setRange(.01, 1)
        self.opacity.setDecimals(2)
        self.opacity.setSingleStep(.05)
        self.color = QPushButton()
        self.color.clicked.connect(self.choose_color)
        self.negative_color = QPushButton()
        self.negative_color.clicked.connect(lambda: self.choose_color("negative_color"))
        self.positive, self.negative = QCheckBox("正等值面"), QCheckBox("负等值面")
        for box in (self.positive, self.negative):
            box.toggled.connect(self.edit)
        for label, widget in (("文件", self.source), ("通道", self.channel), ("显示", self.mode),
                              ("等值", self.level), ("负等值", self.negative_level), ("", self.level_lock),
                              ("显示正值", self.positive), ("显示负值", self.negative),
                              ("正值颜色", self.color), ("负值颜色", self.negative_color), ("平面", self.axis),
                              ("位置", self.position), ("色图", self.palette), ("色标最小", self.low),
                              ("色标最大", self.high), ("透明度", self.opacity)):
            self.form.addRow(label, widget)
        self.toggle = QPushButton()
        self.toggle.clicked.connect(self.toggle_enabled)
        layout.addWidget(self.toggle)
        self.slice_button = QPushButton("显示二维切片")
        self.slice_button.setCheckable(True)
        self.slice_button.clicked.connect(self.slice_requested)
        if family == "elf":
            layout.addWidget(self.slice_button)
        else:
            self.slice_button.hide()
        self.info = QLabel()
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        self.source.currentIndexChanged.connect(self.choose_source)
        self.channel.currentIndexChanged.connect(self.choose_channel)
        for widget in (self.mode, self.axis, self.palette):
            widget.currentIndexChanged.connect(self.edit)
        self.level.valueChanged.connect(lambda: self.edit_level(False))
        self.negative_level.valueChanged.connect(lambda: self.edit_level(True))
        for widget in (self.position, self.low, self.high, self.opacity):
            widget.valueChanged.connect(self.edit)
        self.hide()

    def clear(self):
        self.volumes, self.options = [], None
        self.hide()

    def set_level_step(self, step):
        self.level.setSingleStep(step)
        self.negative_level.setSingleStep(step)

    def edit_level(self, negative):
        if self.options["signed"] and self.level_lock.isChecked():
            source, target = (self.negative_level, self.level) if negative else (self.level, self.negative_level)
            with QSignalBlocker(target):
                target.setValue(-source.value())
        self.edit()

    def lock_levels(self, locked):
        if locked:
            with QSignalBlocker(self.negative_level):
                self.negative_level.setValue(-abs(self.level.value()))
        self.edit()

    def set_slice_visible(self, visible):
        with QSignalBlocker(self.slice_button):
            self.slice_button.setChecked(visible)
        self.slice_button.setText("关闭二维切片" if visible else "显示二维切片")
        if self.options is not None:
            self.slice_rows()
            self.adjustSize()

    def slice_rows(self):
        visible = self.options["mode"] == "slice" or self.slice_button.isChecked()
        for widget in (self.axis, self.position, self.palette, self.low, self.high):
            self.form.setRowVisible(widget, visible)

    def populate(self, volumes, options):
        self.volumes, self.options = volumes, options
        widgets = (self.source, self.channel, self.mode, self.axis, self.palette, self.level, self.negative_level,
                   self.level_lock, self.position, self.low, self.high, self.opacity, self.positive, self.negative)
        blockers = [QSignalBlocker(widget) for widget in widgets]
        self.source.clear()
        self.source.addItems([volume.path.name for volume in volumes])
        self.source.setCurrentIndex(options["source"])
        volume = volumes[options["source"]]
        self.channel.clear()
        self.channel.addItems(volume.names)
        self.channel.setCurrentIndex(options["channel"])
        self.mode.setCurrentIndex(self.mode.findData(options["mode"]))
        self.axis.setCurrentIndex(options["axis"])
        self.palette.setCurrentText(options["palette"])
        self.position.setRange(0, 1) if options["axis"] < 3 else self.position.setRange(-1e5, 1e5)
        self.position.setSuffix("" if options["axis"] < 3 else " Å")
        self.level.setRange(0 if options["signed"] else -1e10, 1e10)
        self.negative_level.setRange(-1e10, 0)
        if options["level_lock"]:
            options["negative_level"] = -abs(options["level"])
        self.level_lock.setChecked(options["level_lock"])
        for key in ("level", "negative_level", "position", "low", "high", "opacity"):
            getattr(self, key).setValue(options[key])
        for key in ("color", "negative_color"):
            getattr(self, key).setText(options[key])
            getattr(self, key).setStyleSheet("background: " + options[key])
        self.positive.setChecked(options["positive"])
        self.negative.setChecked(options["negative"])
        self.form.labelForField(self.level).setText("正等值" if options["signed"] else "等值")
        self.toggle.setText("关闭显示" if options["enabled"] else "启用显示")
        data = volume.channels[options["channel"]]
        self.info.setText(f"{volume.kind} · {volume.unit}\n网格 {data.shape[0]} × {data.shape[1]} × {data.shape[2]}\n范围 {data.min():.6g} ～ {data.max():.6g}\n只显示原晶胞；不修改原始文件。")
        if options["mode"] == "slice" and options["high"] <= options["low"]:
            self.info.setText(self.info.text() + "\n色标最大值需要大于最小值。")
        iso = options["mode"] == "iso"
        for widget in (self.level, self.color):
            self.form.setRowVisible(widget, iso)
        for widget in (self.positive, self.negative, self.negative_color, self.negative_level, self.level_lock):
            self.form.setRowVisible(widget, iso and options["signed"])
        self.slice_rows()
        self.toggle.setEnabled(iso or options["high"] > options["low"])
        self.adjustSize()
        del blockers

    def choose_source(self, index):
        if index < 0:
            return
        active = self.options["enabled"]
        self.options.update(defaults(self.volumes[index]), source=index, enabled=active)
        self.populate(self.volumes, self.options)
        self.changed.emit()

    def choose_channel(self, index):
        if index < 0:
            return
        self.options["channel"] = index
        data = self.volumes[self.options["source"]].channels[index]
        signed = bool(data.min() < 0 < data.max())
        self.options.update(signed=signed, positive=True, negative=signed, level=abs(self.options["level"]),
                            negative_level=-abs(self.options["level"]))
        self.options["low"], self.options["high"] = float(data.min()), float(data.max())
        if self.options["high"] == self.options["low"]:
            self.options["high"] += max(abs(self.options["low"]) * .01, 1e-6)
        self.populate(self.volumes, self.options)
        self.changed.emit()

    def edit(self, *_args):
        axis = self.axis.currentIndex()
        position = self.position.value()
        if axis != self.options["axis"]:
            position = .5 if axis < 3 else 0
        self.options.update(mode=self.mode.currentData(), axis=axis, palette=self.palette.currentText(),
                            positive=self.positive.isChecked(), negative=self.negative.isChecked(),
                            level_lock=self.level_lock.isChecked(),
                            **{key: getattr(self, key).value() for key in ("level", "negative_level", "position", "low", "high", "opacity")})
        self.options["position"] = position
        self.populate(self.volumes, self.options)
        self.changed.emit()

    def choose_color(self, key="color"):
        key = key if isinstance(key, str) else "color"
        color = QColorDialog.getColor(QColor(self.options[key]), self, "等值面颜色")
        if color.isValid():
            self.options[key] = color.name()
            self.populate(self.volumes, self.options)
            self.changed.emit()

    def toggle_enabled(self):
        self.options["enabled"] = not self.options["enabled"]
        self.populate(self.volumes, self.options)
        self.changed.emit()
