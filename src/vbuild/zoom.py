"""Editable percentage for the viewport, relative to its fitted camera."""

from PySide6.QtCore import QEvent, QLocale, Qt, Signal
from PySide6.QtWidgets import QAbstractSpinBox, QDoubleSpinBox


class ZoomPercent(QDoubleSpinBox):
    activated = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.select_on_click = True
        self.lineEdit().installEventFilter(self)
        self.setLocale(QLocale.c())
        self.setDecimals(0)
        self.setRange(1, 1e9)
        self.setPrefix("缩放 ")
        self.setSuffix("%")
        self.setKeyboardTracking(False)
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setToolTip("输入缩放百分比，按回车应用")
        self.setFixedWidth(self.fontMetrics().horizontalAdvance("缩放 1000%") + 16)
        self.setStyleSheet("color: #333; background: rgba(255,255,255,210); border: 0; border-radius: 3px; padding: 2px;")

    def eventFilter(self, watched, event):
        if watched is self.lineEdit():
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self.activated.emit()
                self.setFocus(Qt.FocusReason.MouseFocusReason)
            elif event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton and self.select_on_click:
                self.select_on_click = False
                self.selectAll()
                return True
        return super().eventFilter(watched, event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.select_on_click = True

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.lineEdit().setText(self.prefix() + self.textFromValue(self.value()) + self.suffix())
            self.parentWidget().setFocus()
            event.accept()
            return
        super().keyPressEvent(event)
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.hasAcceptableInput():
            self.parentWidget().setFocus()
