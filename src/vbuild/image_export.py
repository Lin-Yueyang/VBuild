"""Capture the active VTK viewport, including its visible scientific annotations."""

import numpy as np
from PySide6.QtCore import QBuffer, QEventLoop, QIODevice, QThread, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QCheckBox, QColorDialog, QComboBox, QGroupBox, QHBoxLayout, QLabel, QPushButton
from vtkmodules.util.numpy_support import vtk_to_numpy


class ImageOptions(QGroupBox):
    def __init__(self, cell_visible, viewport_size):
        super().__init__("图片设置")
        layout = QHBoxLayout(self)
        self.cell = QCheckBox("显示晶胞边界")
        self.cell.setChecked(bool(cell_visible))
        self.cell.setEnabled(cell_visible is not None)
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
        layout.addWidget(QLabel("分辨率"))
        self.resolution = QComboBox()
        width, height = viewport_size
        for scale in (1, 2, 3, 4):
            self.resolution.addItem(f"{scale}× · {width * scale} × {height * scale} px", scale)
        self.resolution.setCurrentIndex(self.resolution.count() - 1)
        layout.addWidget(self.resolution)
        layout.addStretch()

    def choose_color(self):
        color = QColorDialog.getColor(QColor(self.color.text()), self, "图片背景")
        if color.isValid():
            self.color.setText(color.name().upper())

    def background_color(self):
        return (None, QColor("white"), QColor(self.color.text()))[self.background.currentIndex()]


def paint_labels(painter, labels, viewport_height):
    # The interactive glyph atlas uses screen pixels. Draw font outlines at
    # the original projected anchors so tiled exports keep their alignment.
    indices = vtk_to_numpy(labels.geometry.GetPolys().GetConnectivityArray())[::4] // 4
    for owner in np.unique(labels.owners[indices]):
        cx, cy = labels.screen_centers[owner]
        previous_height, offset = 0, 0
        for line, kind in zip(labels.texts[owner].splitlines(), labels.line_kinds[owner]):
            style, font, metrics = labels.formats[kind]
            line_height = metrics.height() + 2 * labels.margin
            if previous_height:
                offset += (previous_height + line_height) / 2
            previous_height = line_height
            x = cx - sum(metrics.horizontalAdvance(c) for c in line) / 2
            y = viewport_height - cy + offset - line_height / 2 + labels.margin + metrics.ascent()
            path = QPainterPath()
            path.addText(x, y, font, line)
            color = QColor(style['color']) if style['color'] else QColor.fromRgbF(*labels.color)
            if style['width']:
                pen = QPen(QColor(style['outline']), 2 * style['width'] * labels.ratio)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.strokePath(path, pen)
            painter.fillPath(path, color)


def capture_image(scene, cell, background, scale):
    plotter = scene.plotter
    renderer = plotter.renderer
    previous = renderer.GetBackground()
    gradient = renderer.GetGradientBackground()
    alpha = plotter.image_transparent_background
    actor = scene.cell_actor
    visible = actor.GetVisibility() if actor is not None else False
    labels = [group for group in (scene.labels, scene.measure_labels, scene.force_arrows.labels,
              scene.ghost.labels, *scene.coordinate_grid.labels) if group.actor.GetVisibility() and len(group.owners)]
    viewport_width, viewport_height = plotter.window_size
    try:
        if actor is not None:
            actor.SetVisibility(cell)
        renderer.GradientBackgroundOff()
        renderer.SetBackground(*(background or QColor("white")).getRgbF()[:3])
        for group in labels:
            group.project()
            group.actor.VisibilityOff()
        plotter.render()
        pixels = np.ascontiguousarray(plotter.screenshot(transparent_background=background is None, scale=scale))
    finally:
        for group in labels:
            group.actor.VisibilityOn()
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
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.save()
    painter.scale(width / viewport_width, height / viewport_height)
    for group in labels:
        paint_labels(painter, group, viewport_height)
    painter.restore()
    painter.scale(width / plotter.interactor.width(), height / plotter.interactor.height())
    for widget in (scene.cell_info, scene.moment_summary, scene.zoom_label):
        if widget.isVisible():
            widget.render(painter, widget.pos())
    painter.end()
    return image


def png_bytes(image):
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise OSError("PNG 图片编码失败。")
    return bytes(buffer.data())


class PngWriter(QThread):
    def __init__(self, image, path, write):
        super().__init__()
        self.image, self.path, self.write = image, path, write
        self.error = None

    def run(self):
        try:
            self.write(self.path, png_bytes(self.image), overwrite=True)
        except OSError as error:
            self.error = error

    def save(self):
        loop = QEventLoop()
        self.finished.connect(loop.quit)
        self.start()
        loop.exec()
        self.wait()
        if self.error is not None:
            raise self.error
