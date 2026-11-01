"""Structure tabs show directory/file; crowded labels keep their final characters."""

from PySide6.QtCore import QMimeData, QRect, QSize, Qt
from PySide6.QtGui import QDrag, QPainter
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
            target = self.tabAt(point)
            if target >= 0 and (target - index) * (point.x() - origin.x()) > 0:
                self.moveTab(index, target)
                self.press = (target, point)
                self.reordering = True
            return
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
                if self.reordering or index == self.tabAt(event.position().toPoint()):
                    self.setCurrentIndex(index)
            event.accept()
        else:
            super().mouseReleaseEvent(event)

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
                    button.setVisible(index == self.currentIndex() or self.width()/self.count() >= 60)

    def set_double(self, enabled):
        self.double = enabled
        self.updateGeometry()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        for index in range(self.count()):
            option = QStyleOptionTab()
            self.initStyleOption(option, index)
            self.style().drawControl(QStyle.ControlElement.CE_TabBarTabShape, option, painter, self)
            left, right = self.tabButton(index, QTabBar.ButtonPosition.LeftSide), self.tabButton(index, QTabBar.ButtonPosition.RightSide)
            rect = self.tabRect(index).adjusted(26 if left is not None and left.isVisible() else 8, 4,
                                               -26 if right is not None and right.isVisible() else -3, -4)
            lines = self.tabText(index).split("\n")
            height = rect.height() // len(lines)
            for row, text in enumerate(lines):
                self.style().drawItemText(painter, QRect(rect.x(), rect.y()+row*height, rect.width(), height),
                    Qt.AlignmentFlag.AlignCenter, option.palette, self.isEnabled(), tail(text, self.fontMetrics(), rect.width()))
