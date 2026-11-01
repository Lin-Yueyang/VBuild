"""Optional playback controls and a cancellable background frame-index scan."""

from xml.etree.ElementTree import ParseError

from PySide6.QtCore import QSignalBlocker, QThread, QTimer, Signal, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QSlider, QSpinBox, QStyle, QStyleOptionSlider, QToolButton, QVBoxLayout, QWidget


def tick_step(count):
    return max(5, int(count / 50 + 0.5) * 5)


class Timeline(QSlider):
    def __init__(self):
        super().__init__(Qt.Orientation.Horizontal)
        self.setMinimumHeight(48)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def ticks(self):
        return list(range(tick_step(self.maximum()), self.maximum() + 1, tick_step(self.maximum())))

    def style_option(self):
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        option.subControls = QStyle.SubControl.SC_SliderGroove | QStyle.SubControl.SC_SliderHandle
        option.activeSubControls = QStyle.SubControl.SC_SliderHandle if self.isSliderDown() else QStyle.SubControl.SC_None
        option.rect.setTop(max(17, self.height() // 2 - 9))
        option.rect.setHeight(18)
        return option

    def paintEvent(self, _event):
        option = self.style_option()
        painter = QPainter(self)
        self.style().drawComplexControl(QStyle.ComplexControl.CC_Slider, option, painter, self)
        handle = self.style().subControlRect(QStyle.ComplexControl.CC_Slider, option,
                                             QStyle.SubControl.SC_SliderHandle, self)
        painter.setPen(QColor("#718094"))
        values = self.ticks() if self.maximum() >= 5 else list(range(1, self.maximum() + 1))
        for value in values:
            position = QStyle.sliderPositionFromValue(self.minimum(), self.maximum(), value,
                                                      self.width() - handle.width(), option.upsideDown)
            x = position + handle.width() // 2
            y = handle.center().y()
            painter.drawLine(x, y + 9, x, y + 20)
            text = str(value)
            width = painter.fontMetrics().horizontalAdvance(text)
            left = min(self.width() - width, max(0, x - width // 2))
            painter.drawText(left, y - 10, text)

    def move_handle(self, event):
        option = self.style_option()
        handle = self.style().subControlRect(QStyle.ComplexControl.CC_Slider, option,
                                             QStyle.SubControl.SC_SliderHandle, self)
        span = self.width() - handle.width()
        position = min(span, max(0, round(event.position().x() - handle.width() / 2)))
        self.setSliderPosition(QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), position, span, option.upsideDown))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self.setSliderDown(True)
            self.move_handle(event)
            event.accept()

    def mouseMoveEvent(self, event):
        if self.isSliderDown():
            self.move_handle(event)
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setSliderDown(False)
            event.accept()


class IndexThread(QThread):
    ready = Signal(object)
    failed = Signal(str)
    progress = Signal(int)

    def __init__(self, reader, parent):
        super().__init__(parent)
        self.reader = reader

    def run(self):
        # Frame indexing is a file-read boundary; malformed/missing sources are reported.
        try:
            reader = self.reader.index(self.progress.emit, self.isInterruptionRequested)
        except (ValueError, OSError, KeyError, IndexError, ParseError, RuntimeError) as error:
            self.failed.emit(str(error))
            return
        if reader is not None:
            self.ready.emit(reader)


class HeightGrip(QWidget):
    height_changed = Signal(int)

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("height_grip")
        self.setFixedHeight(5)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setToolTip("上下拖动，调整播放轴高度")
        self.setStyleSheet("#height_grip { background: #c9ced5; border-radius: 3px; }")
        self.anchor = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.anchor = (event.globalPosition().y(), self.parentWidget().height())
            event.accept()

    def mouseMoveEvent(self, event):
        if self.anchor is not None:
            y, height = self.anchor
            self.height_changed.emit(round(height + y - event.globalPosition().y()))
            event.accept()

    def mouseReleaseEvent(self, event):
        self.anchor = None
        event.accept()


class Player(QWidget):
    seek = Signal(int)
    speed_changed = Signal(int)
    height_changed = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("player")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoMousePropagation, True)
        self.setStyleSheet("#player { background: #f3f3f3; border-top: 1px solid #c6cbd1; }")
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.advance)
        self.live = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 1, 8, 2)
        layout.setSpacing(2)
        self.grip = HeightGrip(self)
        self.grip.height_changed.connect(self.resize_height)
        layout.addWidget(self.grip)
        self.slider = Timeline()
        self.slider.setObjectName("frame_slider")
        self.slider.setRange(1, 1)
        self.slider.valueChanged.connect(self._seek)
        self.slider.sliderPressed.connect(self.pause)
        layout.addWidget(self.slider)
        row = QHBoxLayout()
        self.play = QToolButton()
        self.play.setObjectName("play")
        self.play.clicked.connect(self.toggle)
        self.play.setToolTip("播放/暂停")
        row.addWidget(self.play)
        self.steps = []
        for label, delta in (("上一帧", -1), ("下一帧", 1)):
            button = QToolButton()
            button.setText("◀" if delta == -1 else "▶")
            button.setToolTip(label)
            button.clicked.connect(lambda _checked, step=delta: self.step(step))
            row.addWidget(button)
            self.steps.append(button)
        row.addWidget(QLabel("帧"))
        self.frame = QSpinBox()
        self.frame.setObjectName("frame_number")
        self.frame.setRange(1, 1)
        self.frame.setKeyboardTracking(False)
        self.frame.valueChanged.connect(self._seek)
        row.addWidget(self.frame)
        self.total = QLabel("/ 1")
        row.addWidget(self.total)
        row.addSpacing(12)
        row.addWidget(QLabel("速度"))
        self.speed = QSpinBox()
        self.speed.setObjectName("playback_speed")
        self.speed.setRange(1, 240)
        self.speed.setValue(20)
        self.speed.setSuffix(" 帧/秒")
        self.speed.valueChanged.connect(self._speed)
        row.addWidget(self.speed)
        self.message = QLabel()
        row.addWidget(self.message, 1)
        layout.addLayout(row)
        self.panel_height = self.sizeHint().height()
        self.pause()
        self.hide()

    def configure(self, count, current, speed, ready, message="", *, live=False):
        self.live = live
        if not ready:
            self.pause()
        for box in (self.slider, self.frame):
            with QSignalBlocker(box):
                box.setRange(1, count)
                box.setValue(current + 1)
            box.setEnabled(ready)
        self.slider.update()
        self.total.setText(f"/ {count}")
        self.play.setEnabled(ready and (count > 1 or live))
        for button in self.steps:
            button.setEnabled(ready and count > 1)
        with QSignalBlocker(self.speed):
            self.speed.setValue(speed)
        interval = round(1000 / speed)
        if self.timer.interval() != interval:
            self.timer.setInterval(interval)
        self.message.setText(message)

    def place(self):
        parent = self.parentWidget()
        height = min(parent.height(), max(self.minimumSizeHint().height(), self.panel_height))
        self.setGeometry(0, parent.height() - height, parent.width(), height)

    def showEvent(self, event):
        super().showEvent(event)
        self.raise_()

    def resize_height(self, height):
        self.panel_height = min(self.parentWidget().height(), max(self.minimumSizeHint().height(), height))
        self.place()
        self.height_changed.emit()

    def _speed(self, number):
        self.timer.setInterval(round(1000 / number))
        self.speed_changed.emit(number)

    def _seek(self, number):
        self.pause()
        self.seek.emit(number - 1)

    def set_frame(self, index):
        for box in (self.slider, self.frame):
            with QSignalBlocker(box):
                box.setValue(index + 1)

    def pause(self):
        self.timer.stop()
        self.play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))

    def toggle(self):
        if self.timer.isActive():
            self.pause()
            return
        self.start()

    def start(self):
        if self.frame.value() == self.frame.maximum():
            self._seek(1)
            if self.frame.value() != 1:
                return
        self.play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
        self.timer.start()

    def advance(self):
        if self.frame.value() < self.frame.maximum():
            self.seek.emit(self.frame.value())
        if self.frame.value() == self.frame.maximum() and not self.live:
            self.pause()

    def step(self, delta):
        self.pause()
        number = min(self.frame.maximum(), max(1, self.frame.value() + delta))
        self.seek.emit(number - 1)
