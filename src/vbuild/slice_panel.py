"""Physical two-dimensional ELF slices; plotting is loaded only when requested."""

from io import BytesIO

import numpy as np
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget


def plane_axes(cell, axis):
    remaining = [index for index in range(3) if index != axis % 3]
    basis = cell[remaining].copy() if axis < 3 else np.eye(3)[remaining]
    basis[0] /= np.linalg.norm(basis[0])
    basis[1] -= np.dot(basis[1], basis[0]) * basis[0]
    basis[1] /= np.linalg.norm(basis[1])
    symbols = "abc" if axis < 3 else "xyz"
    labels = [symbols[index] + (" direction (Å)" if row == 0 else " perpendicular (Å)")
              for row, index in enumerate(remaining)]
    return basis, labels


class SlicePanel(QWidget):
    export_requested = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.layout = QVBoxLayout(self)
        self.caption = QLabel()
        self.caption.setWordWrap(True)
        self.layout.addWidget(self.caption)
        self.export = QPushButton("导出二维切片图片（PNG）")
        self.export.clicked.connect(self.export_requested)
        self.layout.addWidget(self.export)
        self.canvas, self.figure, self.state = None, None, None
        self.hide()

    def clear(self):
        if self.state is None:
            return
        self.state = None
        self.volume, self.options = None, None
        self.triangulation, self.interpolator, self.values = None, None, None
        self.canvas.setToolTip("")
        if self.figure is not None:
            self.figure.clear()
            self.canvas.draw_idle()
        self.export.setEnabled(False)

    def populate(self, volume, options, view):
        state = (id(volume), *(options[key] for key in ("channel", "axis", "position", "palette", "low", "high")))
        if state == self.state:
            return
        if self.canvas is None:
            from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
            from matplotlib.figure import Figure
            self.figure = Figure(figsize=(5, 4), layout="constrained")
            self.canvas = FigureCanvasQTAgg(self.figure)
            self.canvas.setMinimumSize(150, 150)
            self.layout.insertWidget(1, self.canvas, 1)
            self.canvas.mpl_connect("motion_notify_event", self.hover)
        self.figure.clear()
        self.state, self.volume, self.options = state, volume, options.copy()
        axis, position = options["axis"], options["position"]
        self.caption.setText(f"{volume.path.name} · {volume.names[options['channel']]}\n"
                             + (f"{'abc'[axis]} = {position:.5g}" if axis < 3 else f"{'xyz'[axis - 3]} = {position:.5g} Å"))
        self.axes = self.figure.add_subplot()
        mesh = view.slice(volume, options).triangulate()
        valid = mesh.n_cells > 0 and options["high"] > options["low"]
        self.export.setEnabled(valid)
        self.triangulation, self.values = None, None
        if valid:
            from matplotlib.tri import Triangulation, LinearTriInterpolator
            basis, labels = plane_axes(volume.document.structure.cell, axis)
            points = mesh.points @ basis.T
            self.values = np.asarray(mesh.point_data["value"])
            self.triangulation = Triangulation(points[:, 0], points[:, 1], mesh.faces.reshape(-1, 4)[:, 1:])
            self.interpolator = LinearTriInterpolator(self.triangulation, self.values)
            image = self.axes.tripcolor(self.triangulation, self.values, shading="gouraud", cmap=options["palette"],
                                       vmin=options["low"], vmax=options["high"])
            self.figure.colorbar(image, cax=self.axes.inset_axes([1.05, 0, .04, 1]), label="ELF")
            location = f"{'abc'[axis]} = {position:.5g}" if axis < 3 else f"{'xyz'[axis - 3]} = {position:.5g} Å"
            self.axes.set(xlabel=labels[0], ylabel=labels[1], aspect="equal", title=volume.names[options["channel"]] + " · " + location)
            self.axes.margins(0)
        else:
            self.axes.text(.5, .5, "Slice outside cell" if mesh.n_cells == 0 else "Invalid color range",
                           ha="center", va="center", transform=self.axes.transAxes)
        self.canvas.draw_idle()

    def hover(self, event):
        if event.inaxes is not self.axes or self.triangulation is None:
            self.canvas.setToolTip("")
            return
        value = self.interpolator(event.xdata, event.ydata)
        self.canvas.setToolTip("" if np.ma.is_masked(value) else f"u = {event.xdata:.3f} Å\nv = {event.ydata:.3f} Å\nELF = {float(value):.5f}")

    def image_bytes(self):
        from matplotlib.figure import Figure
        # Render a separate figure so repeated exports cannot change the live Qt canvas layout.
        figure = Figure(figsize=(5, 4), layout="constrained")
        axes = figure.subplots()
        options = self.options
        image = axes.tripcolor(self.triangulation, self.values, shading="gouraud", cmap=options["palette"],
                              vmin=options["low"], vmax=options["high"])
        figure.colorbar(image, cax=axes.inset_axes([1.05, 0, .04, 1]), label="ELF")
        axes.set(xlabel=self.axes.get_xlabel(), ylabel=self.axes.get_ylabel(), title=self.axes.get_title(),
                 xlim=self.axes.get_xlim(), ylim=self.axes.get_ylim(), aspect="equal")
        content = BytesIO()
        figure.savefig(content, format="png", dpi=300, facecolor="white", bbox_inches="tight")
        return content.getvalue()
