"""Viewport overlays: drag the title, resize the edges, remember user placement."""

from PySide6.QtCore import QPoint, Qt, Signal, QEvent
from PySide6.QtWidgets import QFrame, QLabel


class Overlay(QFrame):
    geometry_changed = Signal()

    def __init__(self, parent, *, resizable=False, vertical=False):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoMousePropagation, True)
        self.setMouseTracking(True)
        self.manual = False
        self.resizable = resizable
        self.vertical = vertical
        self.drag = None
        self.grip = QLabel(self)
        self.grip.setFixedHeight(24)
        self.grip.setCursor(Qt.CursorShape.SizeAllCursor)
        self.grip.setToolTip("拖动标题移动窗口" + ("；拖动边缘调整大小" if vertical else "；拖动两侧边缘调整宽度" if resizable else ""))
        self.grip.installEventFilter(self)

    def showEvent(self, event):
        super().showEvent(event)
        self.raise_()

    def eventFilter(self, watched, event):
        if watched is self.grip:
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self.raise_()
                self.drag = (event.globalPosition().toPoint(), self.pos())
                event.accept()
                return True
            if event.type() == QEvent.Type.MouseMove and self.drag is not None:
                origin, position = self.drag
                self.move(position + event.globalPosition().toPoint() - origin)
                self.keep_visible()
                event.accept()
                return True
            if event.type() == QEvent.Type.MouseButtonRelease and self.drag is not None:
                self.drag = None
                self.manual = True
                self.geometry_changed.emit()
                return True
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event):
        self.raise_()
        if self.resizable and event.button() == Qt.MouseButton.LeftButton:
            x, y = event.position().x(), event.position().y()
            if x < 7 or x > self.width() - 7:
                self.drag = (event.globalPosition().toPoint(), self.geometry(), x < 7, False)
                event.accept()
            elif self.vertical and (y < 7 or y > self.height()-7):
                self.drag = (event.globalPosition().toPoint(), self.geometry(), y < 7, True)
                event.accept()

    def mouseMoveEvent(self, event):
        if self.resizable and self.drag is not None:
            origin, geometry, leading, vertical = self.drag
            delta = event.globalPosition().toPoint()-origin
            if vertical:
                height = max(self.minimumHeight(), geometry.height()+(-delta.y() if leading else delta.y()))
                self.resize(self.width(), height)
                self.move(geometry.x(), geometry.bottom()-height+1 if leading else geometry.y())
            else:
                width = max(self.minimumWidth(), geometry.width()+(-delta.x() if leading else delta.x()))
                self.resize(width, self.height())
                self.move(geometry.right()-width+1 if leading else geometry.x(), geometry.y())
            self.keep_visible()
        elif self.resizable:
            edge = event.position().x() < 7 or event.position().x() > self.width() - 7
            vertical = self.vertical and (event.position().y() < 7 or event.position().y() > self.height()-7)
            self.setCursor(Qt.CursorShape.SizeHorCursor if edge else Qt.CursorShape.SizeVerCursor if vertical else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, event):
        if self.drag is not None:
            self.drag = None
            self.manual = True
            self.geometry_changed.emit()
        event.accept()

    def keep_visible(self):
        # Keep the draggable title reachable even when the viewport becomes smaller.
        if not self.isVisible():
            return
        self.move(max(0, min(self.x(), self.parentWidget().width() - min(80, self.width()))),
                  max(0, min(self.y(), self.parentWidget().height() - self.grip.height())))

    def place(self, x=12, y=12):
        if not self.manual:
            self.move(x, y)
        self.keep_visible()

    def restore(self, geometry):
        self.manual = True
        self.move(QPoint(*geometry[:2]))
        if self.resizable:
            self.resize(geometry[2], geometry[3] if self.vertical and len(geometry) > 3 else self.height())

    def saved(self):
        return [self.x(), self.y(), self.width(), self.height()] if self.vertical else [self.x(), self.y(), self.width()]
