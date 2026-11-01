"""Cached time curves, exact frame picking, wheel zoom and editable axis ranges."""

from bisect import bisect_left

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal, QSignalBlocker
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QDoubleSpinBox, QFrame, QHBoxLayout,
                              QPushButton, QVBoxLayout, QWidget)

CHANNELS = (("temperature", "温度 (K)", "#e88520", 1),
            ("force", "原子最大受力 (eV/Å)", "#42a077", .1),
            ("energy", "势能 (eV)", "#367ac1", 1))


class Curves(QWidget):
    frame_requested = Signal(int)

    def __init__(self, parent):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumHeight(320)
        self.frames, self.hover = [], None
        self.kind = 'md'
        self.channels = {"temperature", "energy", "force"}
        self.cache_key, self.background = None, None
        self.samples, self.first = None, None
        self.times, self.series = [], {}
        self.ranges, self.time_range = {}, None
        self.area, self.x_range = QRectF(), (0, 1)
        self.chart_drag = None
        self.time_fields = []
        for name in ('最小', '最大'):
            field = QDoubleSpinBox(self)
            field.setDecimals(2)
            field.setSingleStep(1)
            field.setKeyboardTracking(False)
            field.setPrefix(name + ' ')
            field.setToolTip('时间横坐标 · ' + name + '值 (fs)')
            field.valueChanged.connect(self.change_time)
            self.time_fields.append(field)
        self.all_time = QPushButton('全部', self)
        self.all_time.setToolTip('显示已有全部时间数据')
        self.all_time.clicked.connect(self.auto_time)
        self.fields, self.automatic = {}, {}
        for key, label, _color, step in CHANNELS:
            fields = []
            for name in ('最小', '最大'):
                field = QDoubleSpinBox(self)
                field.setRange(-1e12, 1e12)
                field.setDecimals(2)
                field.setSingleStep(step)
                field.setKeyboardTracking(False)
                field.setPrefix(name + ' ')
                field.setToolTip(label + ' · ' + name + '值；最大值须大于最小值')
                field.valueChanged.connect(lambda _value, channel=key: self.change_range(channel))
                fields.append(field)
            self.fields[key] = fields
            button = QPushButton('自动', self)
            button.setToolTip('恢复自动坐标范围')
            button.clicked.connect(lambda _checked=False, channel=key: self.auto_range(channel))
            self.automatic[key] = button

    def sync_samples(self):
        first = self.frames[0] if self.frames else None
        if self.samples is not self.frames or len(self.times) > len(self.frames) or first is not self.first:
            changed = self.samples is not self.frames
            self.samples, self.first = self.frames, first
            self.times = []
            self.series = {key: [] for key, *_rest in CHANNELS}
            if changed:
                self.ranges, self.time_range = {}, None
        for step in self.frames[len(self.times):]:
            self.times.append(step.time_fs if self.kind == 'md' else step.number)
            for key, series in self.series.items():
                value = getattr(step, key)
                series.append(np.nan if value is None else value)

    def set_kind(self, kind):
        if kind == self.kind:
            return
        self.kind, self.samples, self.cache_key = kind, None, None
        for name, field in zip(('最小', '最大'), self.time_fields):
            field.setDecimals(2 if kind == 'md' else 0)
            field.setToolTip(('时间横坐标 (fs)' if kind == 'md' else '优化步数') + ' · ' + name + '值')
        self.all_time.setToolTip('显示已有全部' + ('时间' if kind == 'md' else '优化步骤') + '数据')

    def limits(self, key):
        if key in self.ranges:
            return self.ranges[key]
        y = np.asarray(self.series[key])
        y = y[np.isfinite(y)]
        low, high = (float(y.min()), float(y.max())) if len(y) else (0, 1)
        pad = max((high - low) * .08, abs(high) * .001, .01)
        return (max(0, low - pad) if key != "energy" else low - pad), high + pad

    def time_bounds(self):
        low = self.times[0] if self.times else 0
        high = self.times[-1] if self.times else 1
        return low, high if high > low else low + 1

    def bounded_time(self, low, high):
        first, last = self.time_bounds()
        span = min(max(high - low, min(1e-6, last - first)), last - first)
        low = min(max(low, first), last - span)
        return low, low + span

    def visible_time(self):
        return self.time_bounds() if self.time_range is None else self.bounded_time(*self.time_range)

    def set_time(self, low, high):
        bounds = self.bounded_time(low, high)
        self.time_range = None if bounds == self.time_bounds() else bounds
        self.update()

    def change_time(self, _value):
        low, high = (field.value() for field in self.time_fields)
        valid = low < high
        for field in self.time_fields:
            field.setStyleSheet('' if valid else 'QDoubleSpinBox { border: 1px solid #cf4d4d; }')
        if valid:
            self.set_time(low, high)

    def auto_time(self):
        self.time_range = None
        self.update()

    def nearest(self, x):
        time = self.x_range[0] + (x - self.area.left()) / self.area.width() * (self.x_range[1] - self.x_range[0])
        index = min(bisect_left(self.times, time), len(self.times) - 1)
        if index and abs(self.times[index - 1] - time) < abs(self.times[index] - time):
            index -= 1
        return index

    def mousePressEvent(self, event):
        if self.times and self.area.contains(event.position()) and event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            motion = 'pan' if event.button() == Qt.MouseButton.LeftButton else 'select'
            self.chart_drag = (motion, event.position().x(), event.position().x(), self.visible_time())
            self.hover = None
            self.setCursor(Qt.CursorShape.ClosedHandCursor if motion == 'pan' else Qt.CursorShape.CrossCursor)
            event.accept()

    def mouseMoveEvent(self, event):
        if self.chart_drag is not None:
            motion, start, _current, bounds = self.chart_drag
            x = event.position().x()
            self.chart_drag = (motion, start, x, bounds)
            if motion == 'pan':
                delta = (start - x) / self.area.width() * (bounds[1] - bounds[0])
                self.set_time(bounds[0] + delta, bounds[1] + delta)
            self.update()
            event.accept()
            return
        self.hover = event.position().x() if self.area.contains(event.position()) else None
        self.update()

    def mouseReleaseEvent(self, event):
        if self.chart_drag is None:
            return
        motion, start, _current, bounds = self.chart_drag
        x = event.position().x()
        self.chart_drag = None
        self.unsetCursor()
        if motion == 'select' and abs(x - start) >= 4:
            low, high = sorted((start, x))
            low, high = max(self.area.left(), low), min(self.area.right(), high)
            scale = (bounds[1] - bounds[0]) / self.area.width()
            self.set_time(bounds[0] + (low - self.area.left()) * scale,
                          bounds[0] + (high - self.area.left()) * scale)
        self.update()
        event.accept()

    def leaveEvent(self, event):
        self.hover = None
        self.update()

    def mouseDoubleClickEvent(self, event):
        if self.times and self.area.contains(event.position()) and event.button() == Qt.MouseButton.LeftButton:
            self.chart_drag = None
            self.unsetCursor()
            self.frame_requested.emit(self.nearest(event.position().x()))
            event.accept()

    def wheelEvent(self, event):
        if not self.area.contains(event.position()):
            event.ignore()
            return
        factor = 1.2 ** (-event.angleDelta().y() / 120)
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            low, high = self.x_range
            center = low + (event.position().x() - self.area.left()) / self.area.width() * (high - low)
            self.set_time(center + (low - center) * factor, center + (high - center) * factor)
        else:
            for key in self.channels:
                low, high = self.limits(key)
                center = low + (self.area.bottom() - event.position().y()) / self.area.height() * (high - low)
                self.ranges[key] = (center + (low - center) * factor, center + (high - center) * factor)
        self.update()
        event.accept()

    def change_range(self, key):
        fields = self.fields[key]
        bounds = tuple(field.value() for field in fields)
        valid = bounds[0] < bounds[1]
        for field in fields:
            field.setStyleSheet('' if valid else 'QDoubleSpinBox { border: 1px solid #cf4d4d; }')
        if valid:
            self.ranges[key] = bounds
            self.update()

    def auto_range(self, key):
        self.ranges.pop(key, None)
        self.update()

    def rebuild(self):
        self.background = QPixmap(self.size())
        self.background.fill(QColor("white"))
        painter = QPainter(self.background)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.definitions = [(key, label, color) for key, label, color, _step in CHANNELS if key in self.channels]
        right_axes = max(0, len(self.definitions) - 1)
        inline = self.width() >= 700
        self.header_step = 28 if inline else 52
        self.header_width = self.width() - 354 if inline else self.width() - 12
        self.header_start = 26 if inline else 50
        top = self.header_start + 58 + self.header_step * len(self.definitions)
        self.area = QRectF(70, top, max(1, self.width() - 90 - 78 * right_axes), max(1, self.height() - top - 55))
        self.x_range = self.visible_time()
        left, right = self.x_range
        area = self.area
        editing_time = any(field.hasFocus() for field in self.time_fields)
        for column, (field, value) in enumerate(zip(self.time_fields, self.x_range)):
            with QSignalBlocker(field):
                field.setRange(*self.time_bounds())
                if not editing_time:
                    field.setValue(value)
                    field.setStyleSheet('')
            field.setEnabled(bool(self.times))
            field.setGeometry(self.width() - 342 + column * 147, 0 if inline else 24, 142, 23)
        self.all_time.setGeometry(self.width() - 48, 0 if inline else 24, 42, 23)
        for key, fields in self.fields.items():
            if key not in self.channels:
                for field in fields:
                    field.hide()
                self.automatic[key].hide()
        for row, (key, label, _color) in enumerate(self.definitions):
            y = self.header_start + row * self.header_step + (0 if inline else 24)
            fields = self.fields[key]
            editing = any(field.hasFocus() for field in fields)
            for column, (field, value) in enumerate(zip(fields, self.limits(key))):
                if not editing:
                    with QSignalBlocker(field):
                        field.setValue(value)
                    field.setStyleSheet('')
                field.setGeometry(self.width() - 342 + column * 147, y, 142, 23)
                field.show()
            self.automatic[key].setGeometry(self.width() - 48, y, 42, 23)
            self.automatic[key].show()
        painter.setPen(QColor("#777777"))
        painter.drawRect(area)
        legend = [(key, color) for key, _label, color in self.definitions]
        legend_labels = {'temperature': '温度', 'force': '原子最大受力', 'energy': '势能'}
        x = 6
        for key, color in legend:
            painter.setPen(QPen(QColor(color), 2))
            painter.drawLine(QPointF(x, top - 28), QPointF(x + 18, top - 28))
            painter.setPen(QColor('#333333'))
            label = legend_labels[key]
            painter.drawText(QRectF(x + 23, top - 38, 130, 20), label)
            x += 36 + painter.fontMetrics().horizontalAdvance(label)
        for tick in range(5):
            x = area.left() + area.width() * tick / 4
            time = left + (right - left) * tick / 4
            painter.drawText(QRectF(x - 35, area.bottom() + 5, 70, 18), Qt.AlignmentFlag.AlignCenter, f"{time:.2f}")
        painter.drawText(QRectF(area.left(), area.bottom() + 25, area.width(), 20), Qt.AlignmentFlag.AlignCenter,
                         "时间 (fs)" if self.kind == 'md' else '优化步数')
        for row, (key, label, color) in enumerate(self.definitions):
            low, high = self.limits(key)
            painter.setPen(QColor(color))
            axis_x = area.left() if row == 0 else area.right() + (row - 1) * 78
            painter.drawLine(QPointF(axis_x, area.top()), QPointF(axis_x, area.bottom()))
            for tick in range(3):
                y = area.bottom() - tick / 2 * area.height()
                value = low + (high - low) * tick / 2
                painter.drawText(QRectF(axis_x - 68 if row == 0 else axis_x + 4, y - 10, 65, 20),
                                 Qt.AlignmentFlag.AlignRight if row == 0 else Qt.AlignmentFlag.AlignLeft, f"{value:.2f}")
            painter.drawText(QRectF(6, self.header_start + self.header_step * row, self.header_width, 23), label)
            y = np.asarray(self.series[key])
            keep = np.isfinite(y)
            x, y = np.asarray(self.times)[keep], y[keep]
            start, end = max(0, np.searchsorted(x, left) - 1), min(len(x), np.searchsorted(x, right) + 1)
            x, y = x[start:end], y[start:end]
            if len(y) > 2 * area.width():
                starts = np.unique(np.searchsorted(x, np.linspace(left, right, int(area.width()) + 1)))
                starts = np.unique(np.r_[0, starts[starts < len(y)]])
                y = np.column_stack((np.minimum.reduceat(y, starts), np.maximum.reduceat(y, starts))).ravel()
                x = np.repeat(x[starts], 2)
            path = QPainterPath()
            for index, (time, value) in enumerate(zip(x, y)):
                px = area.left() + (time - left) / (right - left) * area.width()
                py = area.bottom() - (value - low) / (high - low) * area.height()
                path.moveTo(px, py) if index == 0 else path.lineTo(px, py)
            painter.save()
            painter.setClipRect(area)
            painter.setPen(QPen(QColor(color), 1.6))
            painter.drawPath(path)
            painter.restore()
        if not self.definitions:
            painter.drawText(area, Qt.AlignmentFlag.AlignCenter, "勾选需要显示的曲线")
        painter.end()

    def paintEvent(self, event):
        self.sync_samples()
        key = (id(self.frames), len(self.frames), id(self.frames[-1]) if self.frames else None,
               self.width(), self.height(), tuple(sorted(self.channels)), tuple(sorted(self.ranges.items())), self.time_range)
        if key != self.cache_key:
            self.rebuild()
            self.cache_key = key
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self.background)
        if self.chart_drag is not None and self.chart_drag[0] == 'select':
            low, high = sorted(self.chart_drag[1:3])
            painter.save()
            painter.setClipRect(self.area)
            painter.fillRect(QRectF(low, self.area.top(), high - low, self.area.height()), QColor(60, 125, 210, 45))
            painter.setPen(QPen(QColor('#3c7dd2'), 1, Qt.PenStyle.DashLine))
            for x in (low, high):
                painter.drawLine(QPointF(x, self.area.top()), QPointF(x, self.area.bottom()))
            painter.restore()
        if not self.times:
            return
        selected = self.frames[-1] if self.hover is None else self.frames[self.nearest(self.hover)]
        left, right = self.x_range
        value = selected.time_fs if self.kind == 'md' else selected.number
        x = self.area.left() + (value - left) / (right - left) * self.area.width()
        painter.setPen(QPen(QColor("#777777"), 1, Qt.PenStyle.DashLine))
        if self.hover is not None:
            painter.drawLine(QPointF(x, self.area.top()), QPointF(x, self.area.bottom()))
        painter.fillRect(QRectF(0, 0, self.width(), self.area.top() - 46), QColor("white"))
        header = f"{selected.time_fs:.2f} fs · 步 {selected.number}" if self.kind == 'md' else f'步 {selected.number}'
        painter.drawText(QRectF(6, 0, self.header_width, 22), header)
        for row, (key, label, color) in enumerate(self.definitions):
            painter.setPen(QColor(color))
            value = getattr(selected, key)
            text = label if value is None else label + f" · {value:.5f}"
            painter.drawText(QRectF(6, self.header_start + self.header_step * row, self.header_width, 23), text)

    def image_bytes(self):
        from .curve_image import figure
        from io import BytesIO
        self.sync_samples()
        image = figure(self.times, self.series, self.channels, self.visible_time(),
                       {key: self.limits(key) for key in self.channels},
                       x_label='Time (fs)' if self.kind == 'md' else 'Optimization step')
        output = BytesIO()
        image.savefig(output, format='png', dpi=300, facecolor='white')
        return output.getvalue()


class CurvePanel(QFrame):
    channels_changed = Signal(object)
    export_requested = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("curve_panel")
        self.setStyleSheet("#curve_panel { background: white; border: 1px solid #adb3ba; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 8, 9, 8)
        self.plot = Curves(self)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.boxes = {}
        self.choices = {'md': {'temperature', 'force', 'energy'}, 'opt': {'force', 'energy'}}
        for key, label, color, _step in CHANNELS:
            box = QCheckBox(label)
            box.setStyleSheet("color: " + color)
            box.setChecked(True)
            box.toggled.connect(self.filter_channels)
            self.boxes[key] = box
            row.addWidget(box)
        layout.addLayout(row)
        layout.addWidget(self.plot, 1)
        self.export = QPushButton('导出曲线图片（PNG）')
        self.export.setEnabled(False)
        self.export.clicked.connect(self.export_requested)
        layout.addWidget(self.export)
        self.setMinimumWidth(440)
        self.hide()

    def filter_channels(self):
        self.plot.channels = {key for key, box in self.boxes.items() if box.isChecked()
                              and (key != 'temperature' or self.plot.kind == 'md')}
        self.choices[self.plot.kind] = self.plot.channels.copy()
        self.plot.update()
        self.channels_changed.emit(sorted(self.plot.channels))

    def set_kind(self, kind):
        if kind == self.plot.kind:
            return
        self.plot.set_kind(kind)
        self.plot.channels = self.choices[kind].copy()
        for key, box in self.boxes.items():
            with QSignalBlocker(box):
                box.setChecked(key in self.plot.channels)
                box.setVisible(key != 'temperature' or kind == 'md')
        self.plot.update()
