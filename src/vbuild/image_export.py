"""Capture the active VTK viewport, including its visible scientific annotations."""

from base64 import b64encode

import numpy as np
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QCheckBox, QColorDialog, QComboBox, QGroupBox, QHBoxLayout, QLabel, QPushButton


class ImageOptions(QGroupBox):
    def __init__(self, periodic):
        super().__init__("图片设置")
        layout = QHBoxLayout(self)
        self.cell = QCheckBox("显示晶胞边界")
        self.cell.setChecked(periodic)
        self.cell.setEnabled(periodic)
        layout.addWidget(self.cell)
        layout.addWidget(QLabel("背景"))
        self.background = QComboBox()
        self.background.addItems(["透明", "白色", "自选颜色"])
        layout.addWidget(self.background)
        self.color = QPushButton("#FFFFFF")
        self.color.setVisible(False)
        self.background.currentIndexChanged.connect(lambda index: self.color.setVisible(index == 2))
        self.color.clicked.connect(self.choose_color)
        layout.addWidget(self.color)
        layout.addStretch()

    def choose_color(self):
        color = QColorDialog.getColor(QColor(self.color.text()), self, "图片背景")
        if color.isValid():
            self.color.setText(color.name().upper())

    def background_color(self):
        return (None, QColor("white"), QColor(self.color.text()))[self.background.currentIndex()]


def snapshot(scene, cell, background, kind):
    plotter = scene.plotter
    renderer = plotter.renderer
    previous = renderer.GetBackground()
    gradient = renderer.GetGradientBackground()
    alpha = plotter.image_transparent_background
    actor = scene.cell_actor
    visible = actor.GetVisibility() if actor is not None else False
    try:
        if actor is not None:
            actor.SetVisibility(cell)
        renderer.GradientBackgroundOff()
        renderer.SetBackground(*(background or QColor("white")).getRgbF()[:3])
        plotter.render()
        pixels = np.ascontiguousarray(plotter.screenshot(transparent_background=background is None))
    finally:
        if actor is not None:
            actor.SetVisibility(visible)
        renderer.SetBackground(*previous)
        renderer.SetGradientBackground(gradient)
        plotter.image_transparent_background = alpha
        plotter.render()

    height, width, channels = pixels.shape
    fmt = QImage.Format.Format_RGBA8888 if channels == 4 else QImage.Format.Format_RGB888
    image = QImage(pixels.data, width, height, pixels.strides[0], fmt).copy()
    painter = QPainter(image)
    painter.scale(width / plotter.interactor.width(), height / plotter.interactor.height())
    for widget in (scene.cell_info, scene.moment_summary):
        if widget.isVisible():
            widget.render(painter, widget.pos())
    painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise OSError("PNG 图片编码失败。")
    png = bytes(buffer.data())
    if kind == "png":
        return png
    return (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
            f'<image width="{width}" height="{height}" xlink:href="data:image/png;base64,').encode() + b64encode(png) + b'"/></svg>'
