"""Structure tabs show directory/file; crowded labels keep their final characters."""

from PySide6.QtCore import QEasingCurve, QMimeData, QRect, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QDrag, QPainter, QPen
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionTab, QTabBar

STRUCTURE_MIME = "application/x-vbuild-structure"


def tail(text, metrics, width):
    while text and metrics.horizontalAdvance(text) > width:
        text = text[1:]
    return text


class StructureTabs(QTabBar):
    def __init__(self, parent):
        super().__init__(parent)
        self.double = True
        self.press = None
        self.reordering = False
        self.destination = None
        self.drag_image = None
        self.drag_x = 0
        self.offsets, self.starts, self.ends = {}, {}, {}
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(130)
        self.animation.setStartValue(0.)
        self.animation.setEndValue(1.)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.valueChanged.connect(self.animate)
        self.setUsesScrollButtons(False)
        self.setElideMode(Qt.TextElideMode.ElideNone)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            point = event.position().toPoint()
            index = self.tabAt(point)
            self.press = (index, point) if index >= 0 else None
            self.reordering = False
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.press is None:
            return
        index, origin = self.press
        if (event.position().toPoint() - origin).manhattanLength() < QApplication.startDragDistance():
            return
        point = event.position().toPoint()
        if 0 <= point.y() < self.height():
            if not self.reordering:
                self.drag_image = self.grab(self.tabRect(index))
                self.reordering = True
                for i in range(self.count()):
                    for side in (QTabBar.ButtonPosition.LeftSide, QTabBar.ButtonPosition.RightSide):
                        button = self.tabButton(i, side)
                        if button is not None:
                            button.hide()
            self.drag_x = max(0, min(self.width() - self.tabRect(index).width(),
                                    self.tabRect(index).x() + point.x() - origin.x()))
            target = self.tabAt(point)
            if target < 0:
                target = 0 if point.x() < self.tabRect(0).left() else self.count() - 1
            if target != self.destination:
                self.destination = target
                order = [i for i in range(self.count()) if i != index]
                order.insert(target, index)
                self.animation.stop()
                self.starts = self.offsets.copy()
                x = self.tabRect(0).x()
                self.ends = {}
                for i in order:
                    self.ends[i] = x - self.tabRect(i).x()
                    x += self.tabRect(i).width()
                self.animation.start()
            self.update()
            return
        self.end_drag()
        self.press = None
        from .session import Session
        source = self.tabData(index)
        if isinstance(source, Session):
            mime = QMimeData()
            mime.setData(STRUCTURE_MIME, str(id(source)).encode("ascii"))
            drag = QDrag(self)
            drag.setMimeData(mime)
            drag.setPixmap(self.grab(self.tabRect(index)))
            drag.exec(Qt.DropAction.CopyAction)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.press is not None:
                index, _origin = self.press
                self.press = None
                target = self.destination if self.reordering else index
                dragging = self.reordering
                self.end_drag()
                if dragging:
                    self.moveTab(index, target)
                    self.setCurrentIndex(target)
                elif index == self.tabAt(event.position().toPoint()):
                    self.setCurrentIndex(index)
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def animate(self, progress):
        self.offsets = {i: self.starts.get(i, 0) + (target - self.starts.get(i, 0)) * progress
                        for i, target in self.ends.items()}
        self.update()

    def end_drag(self):
        self.animation.stop()
        self.reordering, self.destination, self.drag_image = False, None, None
        self.offsets, self.starts, self.ends = {}, {}, {}
        self.tabLayoutChange()
        self.update()

    def tabSizeHint(self, index):
        lines = self.tabText(index).split("\n")
        width = max((self.fontMetrics().horizontalAdvance(line) for line in lines), default=0) + 38
        return QSize(max(75, min(190, width)), (self.fontMetrics().height() + 14) * (2 if self.double else 1))

    def minimumTabSizeHint(self, index):
        width = 48 if index == self.currentIndex() or self.count() <= 1 else max(1, min(48, (self.width()-48)//(self.count()-1)))
        return QSize(width, self.tabSizeHint(index).height())

    def minimumSizeHint(self):
        return QSize(0, self.tabSizeHint(0).height() if self.count() else 0)

    def tabLayoutChange(self):
        super().tabLayoutChange()
        for index in range(self.count()):
            for side in (QTabBar.ButtonPosition.LeftSide, QTabBar.ButtonPosition.RightSide):
                button = self.tabButton(index, side)
                if button is not None:
                    button.setVisible(not self.reordering and (index == self.currentIndex() or self.width()/self.count() >= 60))

    def set_double(self, enabled):
        self.double = enabled
        self.updateGeometry()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        for index in range(self.count()):
            if self.reordering and self.press is not None and index == self.press[0]:
                continue
            option = QStyleOptionTab()
            self.initStyleOption(option, index)
            offset = round(self.offsets.get(index, 0))
            painter.save()
            painter.translate(offset, 0)
            self.style().drawControl(QStyle.ControlElement.CE_TabBarTabShape, option, painter, self)
            left, right = self.tabButton(index, QTabBar.ButtonPosition.LeftSide), self.tabButton(index, QTabBar.ButtonPosition.RightSide)
            rect = self.tabRect(index).adjusted(26 if left is not None and left.isVisible() else 8, 4,
                                               -26 if right is not None and right.isVisible() else -3, -4)
            lines = self.tabText(index).split("\n")
            height = rect.height() // len(lines)
            for row, text in enumerate(lines):
                self.style().drawItemText(painter, QRect(rect.x(), rect.y()+row*height, rect.width(), height),
                    Qt.AlignmentFlag.AlignCenter, option.palette, self.isEnabled(), tail(text, self.fontMetrics(), rect.width()))
            painter.restore()
        if self.reordering and self.press is not None:
            source = self.press[0]
            slot = self.tabRect(source).translated(round(self.ends.get(source, 0)), 0)
            painter.fillRect(slot.adjusted(1, 2, -1, -2), QColor(211, 230, 249, 150))
            painter.setPen(QPen(QColor('#2782cd'), 3))
            painter.drawLine(slot.topLeft(), slot.bottomLeft())
            painter.setOpacity(.9)
            painter.drawPixmap(self.drag_x, self.tabRect(source).y(), self.drag_image)
