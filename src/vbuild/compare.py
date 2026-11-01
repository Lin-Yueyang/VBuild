"""Comparison pages reference open documents; every populated cell is editable."""

from copy import deepcopy
from pathlib import Path

import numpy as np
from PySide6.QtCore import QEvent, QSignalBlocker, Qt, QTimer
from PySide6.QtWidgets import (QComboBox, QHBoxLayout,
                              QLabel, QFileDialog, QFrame, QSplitter, QToolButton,
                              QVBoxLayout, QWidget)

from .scene import Scene
from .tabs import STRUCTURE_MIME

LAYOUTS = {"一行两列": (1, 2), "一行三列": (1, 3), "两行两列": (2, 2)}


def view_center(scene):
    structure = scene.structure
    if structure.cell is not None:
        return structure.cell.sum(axis=0) / 2
    if len(structure.positions):
        return (structure.positions.min(axis=0) + structure.positions.max(axis=0)) / 2
    return np.zeros(3)


def camera_key(scene):
    camera = scene.plotter.camera
    transform = camera.GetUserViewTransform()
    matrix = () if transform is None else tuple(transform.GetMatrix().GetElement(row, column)
                                                for row in range(4) for column in range(4))
    return (camera.GetPosition(), camera.GetFocalPoint(), camera.GetViewUp(),
            camera.GetParallelProjection(), camera.GetParallelScale(), camera.GetViewAngle(),
            camera.GetWindowCenter(), matrix)


class CompareCell(QFrame):
    def __init__(self, page):
        super().__init__(page)
        self.page, self.source, self.scene, self.key = page, None, None, None
        self.view_key = None
        self.setObjectName("compareCell")
        self.setAcceptDrops(True)
        self.setMinimumSize(100, 100)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(1)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        self.combo = QComboBox()
        self.combo.setMinimumWidth(0)
        self.combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.combo.setToolTip("选择已打开的结构；也可把结构标签拖入此格")
        self.combo.activated.connect(self.choose)
        header.addWidget(self.combo, 1)
        for text, tip, callback in (("＋", "导入文件到此格", self.open_file), ("×", "关闭此格结构，保留原结构标签", self.clear)):
            button = QToolButton()
            button.setText(text)
            button.setToolTip(tip)
            button.setFixedSize(24, 24)
            button.clicked.connect(callback)
            header.addWidget(button)
        layout.addLayout(header)
        self.empty = QLabel("拖入结构标签或文件\n或在上方选择结构")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty, 1)
        self.sync()

    def mark(self, active):
        self.setStyleSheet("QFrame#compareCell { border: 2px solid " + ("#448bdd" if active else "#d6d9dd") + "; }")

    def choose(self, index):
        self.assign(self.combo.itemData(index))

    def assign(self, source):
        window = self.page.window
        if window.builder is not None:
            return
        window._store_current()
        self.source, self.key = source, None
        if source is None:
            self.clear()
            return
        if self.scene is None:
            self.scene = Scene(self)
            window.register_scene(self.scene)
            self.scene.plotter.interactor.installEventFilter(self)
            self.scene.activated.connect(self.activate)
            self.view_observer = self.scene.plotter.render_window.AddObserver("EndEvent", self.view_changed)
            self.layout().addWidget(self.scene, 1)
        self.empty.hide()
        self.scene.show()
        with QSignalBlocker(self.scene):
            self.populate(reset=True)
        self.sync()
        window.activate_cell(self, store=False)

    def activate(self):
        self.page.window.activate_cell(self)

    def mousePressEvent(self, event):
        self.activate()
        super().mousePressEvent(event)

    def open_file(self):
        name, _kind = QFileDialog.getOpenFileName(self.page.window, "导入对比结构", "", "结构文件 (*)")
        if name:
            self.page.window.open_path(Path(name), target=self)

    def clear(self):
        window = self.page.window
        if window.builder is not None:
            return
        active = window.comparison is self.page and self.page.active is self
        if active:
            window._store_current()
            window.change_scene(window.single_scene)
        self.source, self.key = None, None
        self.view_key = None
        if self.scene is not None:
            self.scene.plotter.render_window.RemoveObserver(self.view_observer)
            self.scene.plotter.close()
            self.scene.hide()
            self.scene.deleteLater()
            self.scene = None
        self.empty.show()
        self.sync()
        if active:
            window.clear_document()

    def sync(self):
        with QSignalBlocker(self.combo):
            self.combo.clear()
            self.combo.addItem("选择结构 / 拖入标签", None)
            for source in self.page.window.sessions:
                self.combo.addItem(source.path.parent.name + " / " + source.path.name, source)
            self.combo.setCurrentIndex(next((index for index in range(self.combo.count())
                                            if self.combo.itemData(index) is self.source), 0))
        self.mark(self.page.active is self)
        if self.scene is not None and self.page.window.scene is not self.scene:
            self.populate()

    def populate(self, *, reset=False):
        source, scene, window = self.source, self.scene, self.page.window
        structure = source.document.structure
        view_style = window.view_profile(source)
        run = source.trajectory if source.trajectory is not None and source.trajectory.kind == "neb" else None
        key = (id(source), structure.symbols, structure.positions.tobytes(),
               None if structure.cell is None else structure.cell.tobytes(), source.representation,
               None if structure.free is None else structure.free.tobytes(), source.show_bonds,
               source.show_indices, source.show_fixed, source.show_periodic, source.periods,
               tuple(sorted(source.hidden_elements)), repr(source.render_style), repr(view_style), repr(source.render_atoms), source.render_font or window.settings["index_font"],
               repr(window.settings.get('label_styles', {})),
               source.show_contacts, window.bond_scale, repr(window.bond_ranges),
               None if run is None else (source.frame, tuple(sorted(run.selected)), tuple(sorted(run.opaque)),
                                         run.opacity, run.bond_opacity))
        if key == self.key:
            scene.set_charges(source.bader.labels() if source.bader is not None and source.show_bader
                              and source.bader.matches(structure) else None)
            scene.set_moment_data(source.moments if source.moments is not None and source.show_moments
                              and source.moments.matches(structure) else None)
            return
        self.key = key
        scene.show_periodic, scene.periods = source.show_periodic, source.periods
        scene.show_indices, scene.hidden_elements = source.show_indices, source.hidden_elements.copy()
        scene.show_fixed, scene.show_contacts = source.show_fixed, source.show_contacts
        scene.labels.set_size(source.render_font or window.settings["index_font"])
        window.sync_render_atoms(source)
        scene.background = "#d5d7da" if source.relaxation is not None else None
        scene.bond_visibility = source.show_bonds
        scene.set_look(source.representation, source.render_style, source.render_style is not None, rebuild=False,
                       overrides=source.render_atoms, view_style=view_style)
        scene.set_structure(deepcopy(structure), reset=reset)
        if reset:
            scene.restore_measurement(source.measurement)
        if reset and source.camera is not None:
            scene.restore_camera(source.camera)
        scene.set_mode(source.mode)
        if source.bader is not None and source.show_bader and source.bader.matches(structure):
            scene.set_charges(source.bader.labels())
        if source.moments is not None and source.show_moments and source.moments.matches(structure):
            scene.set_moment_data(source.moments)
        if run is not None:
            from .neb_view import NebView
            if not hasattr(scene, "neb_view"):
                scene.neb_view = NebView(scene)
            scene.neb_view.update(run, source.frame)
            scene.plotter.render()
        elif hasattr(scene, "neb_view"):
            scene.neb_view.hide()
            scene.plotter.render()
        if reset and self.page.sync_view.isChecked() and self.page.linked_camera is not None:
            self.page.apply_view(self)

    def view_changed(self, *_args):
        key = camera_key(self.scene)
        if key != self.view_key:
            self.view_key = key
            self.page.queue_view(self)

    def drag_source(self, mime):
        token = bytes(mime.data(STRUCTURE_MIME))
        return next((source for source in self.page.window.sessions if str(id(source)).encode("ascii") == token), None)

    def accepts(self, mime):
        return (mime.hasFormat(STRUCTURE_MIME) and self.drag_source(mime) is not None
                or len(self.page.window._drop_paths(mime)) == 1)

    def dragEnterEvent(self, event):
        if self.page.window.builder is None and self.accepts(event.mimeData()):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        if self.page.window.builder is not None or not self.accepts(event.mimeData()):
            event.ignore()
            return
        mime = event.mimeData()
        if mime.hasFormat(STRUCTURE_MIME):
            self.assign(self.drag_source(mime))
        else:
            self.page.window.open_path(self.page.window._drop_paths(mime)[0], target=self)
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
            self.dragEnterEvent(event)
            return True
        if event.type() == QEvent.Type.Drop:
            self.dropEvent(event)
            return True
        return super().eventFilter(watched, event)


class Comparison(QWidget):
    def __init__(self, window, shape, number):
        super().__init__(window)
        self.window, self.shape, self.number = window, shape, number
        self.cells, self.active = [], None
        self.linked_camera = None
        self.synchronizing = False
        self.view_timer = QTimer(self)
        self.view_timer.setSingleShot(True)
        self.view_timer.timeout.connect(self.share_view)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        header = QHBoxLayout()
        self.sync_view = QToolButton()
        self.sync_view.setText("同步视角")
        self.sync_view.setCheckable(True)
        self.sync_view.setToolTip("以当前格同步方向、平移和缩放；开启后各格联动，再按关闭")
        self.sync_view.toggled.connect(self.toggle_view)
        header.addWidget(self.sync_view)
        header.addStretch()
        layout.addLayout(header)
        splitter = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(splitter)
        rows, columns = shape
        for _row in range(rows):
            line = QSplitter(Qt.Orientation.Horizontal)
            splitter.addWidget(line)
            for _column in range(columns):
                cell = CompareCell(self)
                self.cells.append(cell)
                line.addWidget(cell)
            line.setSizes([1]*columns)
        splitter.setSizes([1]*rows)
        self.active = self.cells[0]

    def sync(self):
        for cell in self.cells:
            cell.sync()
        self.sync_view.setEnabled(self.active.source is not None)

    def toggle_view(self, checked):
        self.view_timer.stop()
        if checked:
            self.share_view()
        else:
            self.linked_camera = None

    def queue_view(self, cell):
        if (self.sync_view.isChecked() and not self.synchronizing
                and self.window.comparison is self and self.window.scene is cell.scene):
            self.view_timer.start(0)

    def flush_view(self):
        if self.view_timer.isActive():
            self.view_timer.stop()
            self.share_view()

    def share_view(self):
        source = self.active
        if not self.sync_view.isChecked() or source.scene is None:
            return
        camera = source.scene.camera_snapshot()
        center = view_center(source.scene)
        camera.SetPosition(np.asarray(camera.GetPosition()) - center)
        camera.SetFocalPoint(np.asarray(camera.GetFocalPoint()) - center)
        self.linked_camera = camera
        self.synchronizing = True
        for cell in self.cells:
            if cell.scene is not None and cell is not source:
                self.apply_view(cell)
        self.synchronizing = False

    def apply_view(self, cell):
        camera = cell.scene.camera_snapshot()
        camera.DeepCopy(self.linked_camera)
        center = view_center(cell.scene)
        camera.SetPosition(np.asarray(camera.GetPosition()) + center)
        camera.SetFocalPoint(np.asarray(camera.GetFocalPoint()) + center)
        cell.scene.restore_camera(camera, fit_clipping=True)

    def close_views(self):
        self.view_timer.stop()
        for cell in self.cells:
            if cell.scene is not None:
                cell.scene.plotter.render_window.RemoveObserver(cell.view_observer)
                cell.scene.plotter.close()
