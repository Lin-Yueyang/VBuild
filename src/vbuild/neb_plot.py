"""Cached per-image histories and a current-step NEB energy path, with PNG export."""

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal, QSignalBlocker
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .neb_panel import ImageButtons

PALETTE = ("#397fc0", "#e28236", "#349576", "#9b6dc2", "#ce586c", "#a18c3b", "#378f9a", "#7686b5")


def limits(values):
    finite = [float(value) for value in values if value is not None and np.isfinite(value)]
    if not finite:
        return (0., 1.)
    low, high = min(finite), max(finite)
    pad = max((high - low) * .08, abs(high) * 1e-8, .01)
    return low - pad, high + pad


class NebPlot(QWidget):
    jump = Signal(int, int)
    limits_changed = Signal()

    def __init__(self, kind, parent):
        super().__init__(parent)
        self.kind, self.run, self.frame = kind, None, 0
        self.channels, self.relative, self.distance = {"energy", "force"}, True, False
        self.key, self.background, self.hover = None, None, None
        self.series, self.ranges, self.x_range = [], {}, None
        self.setMouseTracking(True)
        self.chart_drag = None
        self.setMinimumHeight(200)

    def populate(self, run, frame):
        if self.run is not run:
            self.ranges, self.x_range, self.key, self.chart_drag = {}, None, None, None
        self.run, self.frame = run, frame
        self.update()

    def reset(self):
        self.ranges, self.x_range, self.key = {}, None, None
        self.update()

    def collect(self):
        run = self.run
        if run is None:
            return []
        indices = sorted(run.analysis_selected)
        if self.kind == "profile":
            x = run.distances(self.frame) if self.distance else np.array([int(image.name) for image in run.images], dtype=float)
            y = (run.relative_energies(self.frame) if self.relative else
                 [image.records[min(self.frame, image.count - 1)].get(run.energy_key) for image in run.images])
            # Keep every path node, with NaN at hidden/missing images to avoid invented segments.
            values = np.array([np.nan if index not in indices or value is None else value for index, value in enumerate(y)])
            return [("energy", "能量路径", x, values, "#397fc0", None)]
        series = []
        for index in indices:
            image, color = run.images[index], PALETTE[index % len(PALETTE)]
            for channel in ("energy", "force"):
                if channel not in self.channels:
                    continue
                field = run.energy_key if channel == "energy" else "max_force"
                y = np.array([record.get(field, np.nan) for record in image.records], dtype=float)
                x = np.arange(1, image.count + 1, dtype=float)
                if image.count == 1 and image.reader is None and len(run.offsets) > 1:
                    x, y = np.array([1., float(len(run.offsets))]), np.repeat(y, 2)
                series.append((channel, image.name, x, y, color, index))
        return series

    def rebuild(self):
        self.series = self.collect()
        points = (self.run.distances(self.frame) if self.kind == "profile" and self.distance else
                  [int(image.name) for image in self.run.images] if self.kind == "profile" else
                  [1, len(self.run.offsets)])
        self.bounds = (float(min(points)), float(max(points)))
        if self.bounds[0] == self.bounds[1]:
            self.bounds = (self.bounds[0] - .5, self.bounds[1] + .5)
        self.x_limits = self.bounds if self.x_range is None else self.bounded_x(*self.x_range)
        self.y_limits = {key: self.ranges.get(key, limits([value for channel, _name, _x, y, _color, _image in self.series if channel == key for value in y]))
                         for key in ("energy", "force")}
        self.area = QRectF(80, 38, max(1, self.width() - 160), max(1, self.height() - 116))
        self.background = QPixmap(self.size())
        self.background.fill(QColor("white"))
        painter = QPainter(self.background)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor("#4b5663"))
        painter.setPen(QColor("#4b5663"))
        painter.drawText(12, 23, "ΔE (eV)" if self.kind == "profile" and self.relative else "能量 (eV)")
        if self.kind == "history" and "force" in self.channels:
            painter.drawText(self.width()-117, 23, "最大受力 (eV/Å)")
        for index in range(5):
            fraction = index / 4
            y = self.area.bottom() - fraction * self.area.height()
            painter.setPen(QPen(QColor("#e6e9ed"), 1))
            painter.drawLine(QPointF(self.area.left(), y), QPointF(self.area.right(), y))
            painter.setPen(QColor("#6e7780"))
            for key, left in (("energy", True), ("force", False)):
                if key == "force" and (self.kind == "profile" or "force" not in self.channels):
                    continue
                low, high = self.y_limits[key]
                label = f"{low + fraction * (high - low):.5g}"
                painter.drawText(QRectF(0 if left else self.area.right()+6, y-9, 74, 18),
                                 Qt.AlignmentFlag.AlignRight if left else Qt.AlignmentFlag.AlignLeft, label)
            x = self.area.left() + fraction * self.area.width()
            low, high = self.x_limits
            painter.drawText(QRectF(x-40, self.area.bottom()+6, 80, 18), Qt.AlignmentFlag.AlignCenter,
                             f"{low + fraction * (high-low):.3g}")
        painter.drawRect(self.area)
        painter.drawText(QRectF(0, self.area.bottom()+27, self.width(), 18), Qt.AlignmentFlag.AlignCenter,
                         "反应坐标 · RMS 路径长度 (Å)" if self.kind == "profile" and self.distance else
                         "图像序号" if self.kind == "profile" else "优化帧（从 1 开始） · 能量实线 / 受力虚线")
        painter.save()
        painter.setClipRect(self.area.adjusted(-3, -3, 3, 3))
        for channel, _name, x, y, color, _image in self.series:
            painter.setPen(QPen(QColor(color), 2, Qt.PenStyle.DashLine if channel == "force" else Qt.PenStyle.SolidLine))
            path, connected, previous = QPainterPath(), False, None
            # Thin histories by screen column while retaining minima/maxima in each bin.
            finite = np.where(np.isfinite(y))[0]
            if len(finite) > self.area.width() * 3:
                bins = np.clip(((x[finite]-self.x_limits[0]) / (self.x_limits[1]-self.x_limits[0]) * self.area.width()).astype(int), 0, int(self.area.width()))
                keep = []
                for chunk in np.split(finite, np.where(np.diff(bins) != 0)[0]+1):
                    keep.extend(sorted({int(chunk[0]), int(chunk[-1]), int(chunk[np.argmin(y[chunk])]), int(chunk[np.argmax(y[chunk])])}))
                finite = np.array(keep)
            for index in range(len(x)) if self.kind == "profile" else finite:
                if not np.isfinite(y[index]):
                    connected = False
                    continue
                point = self.point(x[index], y[index], channel)
                if previous is not None and not np.isfinite(y[previous+1:index]).all():
                    connected = False
                if connected:
                    path.lineTo(point)
                else:
                    path.moveTo(point)
                    connected = True
                previous = index
                if self.kind == "profile":
                    painter.setBrush(QColor(color))
                    painter.drawEllipse(point, 4, 4)
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    label = self.run.images[index].name
                    width = painter.fontMetrics().horizontalAdvance(label)
                    x_label = point.x()+5 if point.x()+width+8 < self.area.right() else point.x()-width-5
                    y_label = min(self.area.bottom()-5, max(self.area.top()+15, point.y()-6))
                    painter.drawText(QPointF(x_label, y_label), label)
            painter.drawPath(path)
        painter.restore()
        if not any(np.isfinite(y).any() for _key, _name, _x, y, _color, _image in self.series):
            painter.drawText(self.area, Qt.AlignmentFlag.AlignCenter, "当前选择没有对应能量/力数据")
        painter.end()
        self.limits_changed.emit()

    def point(self, x, y, channel):
        x0, x1 = self.x_limits
        y0, y1 = self.y_limits[channel]
        return QPointF(self.area.left() + (x-x0)/(x1-x0)*self.area.width(),
                       self.area.bottom() - (y-y0)/(y1-y0)*self.area.height())

    def paintEvent(self, _event):
        if self.run is None:
            return
        chosen = self.run.analysis_selected
        key = (id(self.run), self.width(), self.height(), tuple(sorted(chosen)), self.run.energy_key,
               self.frame if self.kind == "profile" else None, tuple(sorted(self.channels)), self.relative,
               self.distance, self.x_range, tuple(self.ranges.items()))
        if key != self.key:
            self.rebuild()
            self.key = key
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self.background)
        if self.kind == "history":
            x = self.point(self.frame+1, self.y_limits["energy"][0], "energy").x()
            painter.setPen(QPen(QColor("#a0a8b3"), 1))
            painter.drawLine(QPointF(x, self.area.top()), QPointF(x, self.area.bottom()))
        if self.chart_drag is not None and self.chart_drag[0] == "select":
            _motion, first, last, _bounds = self.chart_drag
            left, right = sorted((first, last))
            painter.fillRect(QRectF(max(left, self.area.left()), self.area.top(),
                                   max(0, min(right, self.area.right())-max(left, self.area.left())), self.area.height()), QColor(54, 122, 193, 45))
        if self.hover is not None:
            painter.setPen(QColor("#4b5663"))
            painter.drawText(QRectF(10, self.height()-29, self.width()-20, 26), Qt.AlignmentFlag.AlignCenter, self.hover_text())
        painter.end()

    def selected_point(self, position):
        value = self.x_limits[0] + (position.x()-self.area.left())/self.area.width() * (self.x_limits[1]-self.x_limits[0])
        if self.kind == "profile":
            indices = sorted(self.run.analysis_selected)
            if not indices:
                return self.frame, self.run.active
            x = self.series[0][2]
            image = min(indices, key=lambda index: abs(x[index]-value))
            return self.frame, image
        frame = min(len(self.run.offsets)-1, max(0, round(value)-1))
        closest = []
        for channel, _name, _x, _y, _color, image in self.series:
            record = self.run.images[image].records[min(frame, self.run.images[image].count-1)]
            y = record.get(self.run.energy_key if channel == "energy" else "max_force")
            if y is not None:
                closest.append((abs(self.point(frame+1, y, channel).y()-position.y()), image))
        return frame, min(closest)[1] if closest else self.run.active

    def hover_text(self):
        frame, index = self.hover
        image = self.run.images[index]
        record = image.records[min(frame, image.count-1)]
        energy, force = record.get(self.run.energy_key), record.get("max_force")
        return f"图像 {image.name} · 帧 {min(frame, image.count-1)+1}   E: " + ("—" if energy is None else f"{energy:.6f} eV") + "   |F|max: " + ("—" if force is None else f"{force:.4f} eV/Å")

    def bounded_x(self, low, high):
        first, last = self.bounds
        span = min(max(high-low, 1e-6), last-first)
        low = min(max(low, first), last-span)
        return low, low+span

    def set_x(self, low, high):
        self.x_range = self.bounded_x(low, high)
        self.update()

    def mousePressEvent(self, event):
        if self.run is not None and hasattr(self, "area") and self.area.contains(event.position()) and event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            motion = "pan" if event.button() == Qt.MouseButton.LeftButton else "select"
            self.chart_drag = (motion, event.position().x(), event.position().x(), self.x_limits)
            self.hover = None
            self.setCursor(Qt.CursorShape.ClosedHandCursor if motion == "pan" else Qt.CursorShape.CrossCursor)
            event.accept()

    def mouseMoveEvent(self, event):
        if self.chart_drag is not None:
            motion, start, _current, bounds = self.chart_drag
            x = event.position().x()
            self.chart_drag = (motion, start, x, bounds)
            if motion == "pan":
                delta = (start-x)/self.area.width()*(bounds[1]-bounds[0])
                self.set_x(bounds[0]+delta, bounds[1]+delta)
            self.update()
            event.accept()
            return
        chosen = set() if self.run is None else self.run.analysis_selected
        self.hover = self.selected_point(event.position()) if chosen and hasattr(self, "area") and self.area.contains(event.position()) else None
        self.update()

    def mouseReleaseEvent(self, event):
        if self.chart_drag is None:
            return
        motion, start, _current, bounds = self.chart_drag
        x = event.position().x()
        self.chart_drag = None
        self.unsetCursor()
        if motion == "select" and abs(x-start) >= 4:
            low, high = sorted((start, x))
            low, high = max(self.area.left(), low), min(self.area.right(), high)
            scale = (bounds[1]-bounds[0])/self.area.width()
            self.set_x(bounds[0]+(low-self.area.left())*scale, bounds[0]+(high-self.area.left())*scale)
        self.update()
        event.accept()

    def leaveEvent(self, _event):
        self.hover = None
        self.update()

    def mouseDoubleClickEvent(self, event):
        if self.run is not None and self.area.contains(event.position()) and event.button() == Qt.MouseButton.LeftButton:
            self.chart_drag = None
            self.unsetCursor()
            self.jump.emit(*self.selected_point(event.position()))

    def wheelEvent(self, event):
        if self.run is None or not hasattr(self, "area") or not self.area.contains(event.position()):
            event.ignore()
            return
        scale = .8 if event.angleDelta().y() > 0 else 1.25
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            low, high = self.x_limits
            center = low+(event.position().x()-self.area.left())/self.area.width()*(high-low)
            self.set_x(center+(low-center)*scale, center+(high-center)*scale)
        else:
            for channel in (self.channels if self.kind == "history" else {"energy"}):
                low, high = self.y_limits[channel]
                middle, half = (low+high)/2, (high-low)*scale/2
                self.ranges[channel] = (middle-half, middle+half)
        self.update()
        event.accept()

    def export(self, path):
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        self.rebuild()
        fig = Figure(figsize=(8, 5), layout="constrained")
        FigureCanvasAgg(fig)
        primary = fig.add_subplot()
        secondary = primary.twinx() if self.kind == "history" and "force" in self.channels else None
        for channel, name, x, y, color, _image in self.series:
            axis = secondary if channel == "force" else primary
            axis.plot(x, y, color=color, linestyle="--" if channel == "force" else "-",
                      marker="o" if self.kind == "profile" else None,
                      label="Energy path" if self.kind == "profile" else name + (" |F|" if channel == "force" else " E"))
        primary.set_xlim(*self.x_limits)
        primary.set_ylim(*self.y_limits["energy"])
        primary.set_ylabel("Relative energy (eV)" if self.kind == "profile" and self.relative else "Energy (eV)")
        primary.set_xlabel("RMS path length (Å)" if self.kind == "profile" and self.distance else "Image" if self.kind == "profile" else "Optimization frame")
        if secondary is not None:
            secondary.set_ylim(*self.y_limits["force"])
            secondary.set_ylabel("Maximum atomic force (eV/Å)")
        handles, names = primary.get_legend_handles_labels()
        if secondary is not None:
            extra, labels = secondary.get_legend_handles_labels()
            handles, names = handles+extra, names+labels
        if self.kind == "history":
            fig.legend(handles, names, loc="outside upper center", ncols=4, fontsize=8)
        else:
            _key, _name, x, y, _color, _image = self.series[0]
            for index, value in enumerate(y):
                if np.isfinite(value):
                    primary.annotate(self.run.images[index].name, (x[index], value),
                                     xytext=(-6 if index == len(y)-1 else 6, 6), textcoords="offset points",
                                     ha="right" if index == len(y)-1 else "left", fontsize=9, annotation_clip=True)
        primary.grid(alpha=.2)
        fig.savefig(path, dpi=300)


class NebCurvePanel(QFrame):
    export_requested = Signal(str)
    selection_changed = Signal()

    def __init__(self, kind, parent):
        super().__init__(parent)
        self.kind = kind
        layout, row = QVBoxLayout(self), QHBoxLayout()
        self.plot = NebPlot(kind, self)
        self.images = ImageButtons(self)
        self.images.changed.connect(self.selection_changed)
        layout.addWidget(self.images)
        if kind == "history":
            for key, text in (("energy", "能量"), ("force", "原子最大受力")):
                box = QCheckBox(text)
                box.setChecked(True)
                box.toggled.connect(lambda checked, name=key: self.channel(name, checked))
                row.addWidget(box)
        else:
            self.relative = QCheckBox("相对能量")
            self.relative.setChecked(True)
            self.relative.setToolTip("以第一个具有所选能量的图像为零点；缺失端点能量不补值。")
            self.relative.toggled.connect(self.relative_changed)
            row.addWidget(self.relative)
            self.axis = QComboBox()
            self.axis.addItems(["图像序号", "路径长度 (Å)"])
            self.axis.currentIndexChanged.connect(self.axis_changed)
            row.addWidget(self.axis)
        layout.addLayout(row)
        self.fields, self.axis_rows = {}, {}
        for key, label, step in (("x", "横坐标", 1), ("energy", "能量 (eV)", 1), ("force", "最大受力 (eV/Å)", .1)):
            widget, fields = QWidget(), []
            controls = QHBoxLayout(widget)
            controls.setContentsMargins(0, 0, 0, 0)
            text = QLabel(label)
            text.setFixedWidth(110)
            controls.addWidget(text)
            for name in ("最小", "最大"):
                field = QDoubleSpinBox()
                field.setRange(-1e12, 1e12)
                field.setDecimals(2)
                field.setSingleStep(step)
                field.setKeyboardTracking(False)
                field.setPrefix(name + " ")
                field.setToolTip(label + " · " + name + "值")
                field.valueChanged.connect(lambda _value, axis=key: self.change_range(axis))
                fields.append(field)
                controls.addWidget(field, 1)
            automatic = QPushButton("自动")
            automatic.clicked.connect(lambda _checked=False, axis=key: self.auto_range(axis))
            controls.addWidget(automatic)
            self.fields[key], self.axis_rows[key] = fields, widget
            layout.addWidget(widget)
            widget.setVisible(key != "force" or kind == "history")
        self.plot.limits_changed.connect(self.sync_fields)
        layout.addWidget(self.plot, 1)
        controls = QHBoxLayout()
        reset, export = QPushButton("自动范围"), QPushButton("导出图片")
        reset.clicked.connect(self.plot.reset)
        export.clicked.connect(lambda: self.export_requested.emit(kind))
        controls.addWidget(reset)
        controls.addWidget(export)
        layout.addLayout(controls)
        self.hide()


    def populate(self, run, frame):
        self.plot.populate(run, frame)
        self.images.populate(run)
        if self.kind == "history":
            for index, button in self.images.buttons.items():
                color = PALETTE[index % len(PALETTE)]
                button.setStyleSheet("QPushButton { color: #333; background: white; border: 1px solid #d6dae0; "
                                    "border-bottom: 3px solid " + color + "; border-radius: 4px; padding: 3px; } "
                                    "QPushButton:checked { color: white; background: #0068bf; }")

    def sync_fields(self):
        ranges = {"x": self.plot.x_limits, **self.plot.y_limits}
        for key, fields in self.fields.items():
            if any(field.hasFocus() for field in fields):
                continue
            for field, value in zip(fields, ranges[key]):
                with QSignalBlocker(field):
                    field.setValue(value)
                    field.setStyleSheet("")

    def change_range(self, key):
        low, high = (field.value() for field in self.fields[key])
        valid = low < high
        for field in self.fields[key]:
            field.setStyleSheet("" if valid else "QDoubleSpinBox { border: 1px solid #cf4d4d; }")
        if valid:
            if key == "x":
                self.plot.set_x(low, high)
            else:
                self.plot.ranges[key] = (low, high)
                self.plot.update()

    def auto_range(self, key):
        if key == "x":
            self.plot.x_range = None
        else:
            self.plot.ranges.pop(key, None)
        self.plot.update()

    def channel(self, name, checked):
        self.plot.channels.add(name) if checked else self.plot.channels.discard(name)
        self.axis_rows[name].setVisible(checked)
        self.plot.update()

    def relative_changed(self, checked):
        self.plot.relative = checked
        self.plot.reset()

    def axis_changed(self, index):
        self.plot.distance = bool(index)
        for field in self.fields["x"]:
            field.setSingleStep(.1 if index else 1)
        self.plot.reset()
