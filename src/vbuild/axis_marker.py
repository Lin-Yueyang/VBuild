"""A draggable orientation inset, positioned within the unobstructed viewport."""

import numpy as np
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt


class AxisMarker:
    def __init__(self, scene):
        self.scene, self.widget = scene, scene.plotter.renderer.axes_widget
        self.anchor = [0., 1.]
        self.bottom = None
        self.drag = None
        self.hover = False
        self.rect = QRectF()

    def place(self, bottom=None):
        parent = self.scene.plotter.interactor
        width, height = parent.width(), parent.height()
        self.bottom = bottom
        available = height if bottom is None else min(height, bottom)
        size = max(1, min(164, width - 16, available - 16))
        self.travel = np.maximum([width - size - 24, available - size - 24], 1)
        # The inset contains empty margins; allow those margins beyond the viewport.
        self.minimum = np.full(2, -size * .25)
        self.maximum = np.maximum(self.minimum, np.array([width, available]) - size * .75)
        x, y = np.clip(12 + self.travel * self.anchor, self.minimum, self.maximum)
        self.rect = QRectF(x, y, size, size)
        if width > 0 and height > 0:
            viewport = (x / width, 1 - (y + size) / height, (x + size) / width, 1 - y / height)
            if not np.allclose(self.widget.GetViewport(), viewport):
                self.widget.SetViewport(*viewport)
                self.scene.plotter.render()

    def event(self, event):
        kind = event.type()
        parent = self.scene.plotter.interactor
        margin = self.rect.width() * .12
        target = self.rect.adjusted(margin, margin, -margin, -margin)
        if kind == QEvent.Type.Resize:
            # The main window supplies the playback boundary after its own layout.
            self.place(self.bottom)
        elif kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            if self.widget.GetEnabled() and target.contains(event.position()):
                self.drag = (QPointF(event.position()), self.rect.topLeft())
                parent.setCursor(Qt.CursorShape.ClosedHandCursor)
                self.scene.cancel_gesture()
                return True
        elif kind == QEvent.Type.MouseMove and self.drag is not None:
            start, origin = self.drag
            point = origin + event.position() - start
            position = np.clip([point.x(), point.y()], self.minimum, self.maximum)
            self.anchor = ((position - 12) / self.travel).tolist()
            self.place(self.bottom)
            return True
        elif kind == QEvent.Type.MouseButtonRelease and self.drag is not None:
            self.drag = None
            self.scene.axis_moved.emit(self.anchor.copy())
            parent.setCursor(Qt.CursorShape.OpenHandCursor)
            return True
        elif kind == QEvent.Type.MouseMove:
            hover = bool(self.widget.GetEnabled() and target.contains(event.position()))
            if hover:
                parent.setCursor(Qt.CursorShape.OpenHandCursor)
            elif self.hover:
                parent.unsetCursor()
            self.hover = hover
        elif kind in (QEvent.Type.FocusOut, QEvent.Type.WindowDeactivate, QEvent.Type.UngrabMouse):
            self.drag = None
            parent.unsetCursor()
            self.hover = False
        return False
