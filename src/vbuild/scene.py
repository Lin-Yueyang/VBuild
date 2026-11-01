"""One batched viewport for structures, frame display and screen-plane dragging."""

from itertools import product
from copy import deepcopy

import numpy as np
import pyvista as pv
from ase.data import atomic_numbers
from PySide6.QtCore import QEvent, QPoint, QRect, Signal, Qt, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QRubberBand, QVBoxLayout, QWidget
from pyvistaqt import QtInteractor
from vtkmodules.util.numpy_support import numpy_to_vtk
from vtkmodules.vtkCommonCore import vtkPoints
from vtkmodules.vtkCommonDataModel import vtkPolyData
from vtkmodules.vtkInteractionStyle import vtkInteractorStyleTrackballCamera
from vtkmodules.vtkCommonTransforms import vtkTransform
from vtkmodules.vtkRenderingCore import vtkCamera, vtkTextActor

from .model import Structure
from .measure import diagram, label_position, measure, SIZES
from .bonds import bond_halves, find_bonds, connected
from .paste import PastePreview
from .fixed import FixedMarkers
from .display import AtomLabels
from .view import axis_up, periodic_images, rotate_vector
from .wall_view import WallPreview, WaterRange
from .glyph import atom_actors, line_actor, set_lines
from .visual import Visual, Stage
from .look import color_rgb, default_style
from .contacts import short_pairs
from .force_view import ForceArrows
from .ghost import MoveGhost
from .moment_view import MomentSummary
from .volume_view import VolumeView


class Scene(QWidget):
    activated = Signal()
    selection_changed = Signal(list)
    move_requested = Signal(list, object)
    measurement_changed = Signal()
    paste_requested = Signal(object)
    paste_changed = Signal()
    geometry_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.plotter = QtInteractor(self, auto_update=False, off_screen=False,
                                    multi_samples=0)
        layout.addWidget(self.plotter.interactor)
        self.plotter.set_background("white")
        self.plotter.renderer.SetUseFXAA(True)
        self.plotter.camera.parallel_projection = True
        axes = self.plotter.add_axes(interactive=False, line_width=3, cone_radius=.5,
                                     shaft_length=.72, tip_length=.28, label_size=(.28, .13),
                                     xlabel="+X", ylabel="+Y", zlabel="+Z")
        axes.SetShaftTypeToCylinder()
        axes.SetCylinderRadius(.025)
        self.axis_hint = vtkTextActor()
        self.axis_hint.GetPositionCoordinate().SetCoordinateSystemToNormalizedViewport()
        self.axis_hint.SetPosition(.78, .08)
        self.axis_hint.GetTextProperty().SetFontSize(round(11 * self.plotter.interactor.devicePixelRatioF()))
        self.axis_hint.GetTextProperty().SetColor(.4, .4, .4)
        self.axis_hint.PickableOff()
        self.axis_hint.UseBoundsOff()
        self.plotter.renderer.axes_widget.GetRenderer().AddViewProp(self.axis_hint)
        self.mode = "rotate"
        self.previewing = False
        self.selection: list[int] = []
        self.structure: Structure | None = None
        self.displayed = np.empty((0, 3))
        self.images = np.empty((0, 3))
        self.sources = np.empty(0, dtype=int)
        self.show_periodic = False
        self.periods = (1, 1, 1)
        self.show_indices = False
        self.charge_labels = None
        self.moment_labels = None
        self.moment_summary = MomentSummary(self.plotter.interactor)
        self.moment_summary.move(12, 12)
        self.moment_summary.hide()
        self.hidden_elements = set()
        self.drag = None
        self.camera_drag = None
        self.box = None
        self.click = None
        self.measurement = []
        self.measurement_kind = "distance"
        self.measurement_topology = ()
        self.measure_decimals = 2
        self.measurement_text = ""
        self.rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self.plotter.interactor)
        self.cell_actor = None
        self.bond_scale = 1.15
        self.bond_radius = .12
        self.bond_ranges = {}
        self.view_style = default_style()
        self.bond_visibility = None
        self.background = None
        self.bond_data, self.bond_actor = line_actor(self.plotter.renderer, (1, 1, 1), 15)
        self.bond_actor.GetMapper().SetScalarModeToUseCellData()
        self.bond_actor.GetMapper().SetColorModeToDirectScalars()
        self.measure_data, self.measure_actor = line_actor(self.plotter.renderer, (0.55, 0.16, 0.72), 8)
        self.measure_actor.PickableOff()
        self.measure_actor.UseBoundsOff()
        self.measure_labels = AtomLabels(self.plotter.renderer, pixel_ratio=self.plotter.interactor.devicePixelRatioF, kind='measure')
        self.measure_labels.set_color((0.55, 0.16, 0.72))
        self.bonds = np.empty((0, 2), dtype=int)

        self.points = vtkPoints()
        self.data = vtkPolyData()
        self.data.SetPoints(self.points)
        self.actor, self.metal_actor = atom_actors(self.plotter.renderer, self.data)
        self.mapper = self.actor.GetMapper()
        self.visual = Visual(self.plotter.renderer, self.data, (self.actor, self.metal_actor), self.bond_data, self.bond_actor)
        self.stage = None
        self.labels = AtomLabels(self.plotter.renderer, pixel_ratio=self.plotter.interactor.devicePixelRatioF)
        self.paste = PastePreview(self.plotter.renderer)
        self.ghost = MoveGhost(self.plotter.renderer)
        for actor in (self.actor, self.metal_actor, self.paste.actor, self.paste.metal_actor,
                      self.ghost.preview.actor, self.ghost.preview.metal_actor):
            shader = actor.GetShaderProperty()
            shader.GetFragmentCustomUniforms().SetUniformf("viewReflection", 1)
            # A reflected view reverses winding; VTK's back-face normal flip must be undone.
            shader.AddFragmentShaderReplacement("//VTK::Normal::Impl", True,
                "//VTK::Normal::Impl\nnormalVCVSOutput *= viewReflection;\n", False)
        self.fixed = FixedMarkers(self.plotter.renderer)
        self.force_arrows = ForceArrows(self.plotter.renderer)
        self.volume_views = {name: VolumeView(self.plotter) for name in ("charge", "elf")}
        self.molecule_modifier = Qt.KeyboardModifier.ShiftModifier
        self.molecule_pressed = False
        self.walls = WallPreview(self.plotter.renderer)
        self.show_fixed = False
        self.show_contacts = False
        self.contact_factor = 0.30
        self.contact_rows = np.empty((0, 4))
        self.contact_missing = ()
        self.contacts = FixedMarkers(self.plotter.renderer)
        self.contacts.actor.GetProperty().SetColor(1, 0.25, 0.05)
        self.contacts.actor.GetProperty().SetLineWidth(3)
        self.contacts.actor.UseBoundsOff()
        self.water_walls = WaterRange(self.plotter.renderer)

        self.interactor = self.plotter.iren.interactor
        self.plotter.iren.clear_key_event_callbacks()
        self.style = vtkInteractorStyleTrackballCamera()
        self.interactor.SetInteractorStyle(self.style)
        self.plotter.interactor.installEventFilter(self)
        self.plotter.interactor.setMouseTracking(True)

    def eventFilter(self, watched, event):
        if watched is self.plotter.interactor and event.type() in (
                QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick, QEvent.Type.Wheel):
            self.activated.emit()
        if watched is self.plotter.interactor and event.type() in (
                QEvent.Type.FocusOut, QEvent.Type.WindowDeactivate, QEvent.Type.UngrabMouse):
            self.cancel_gesture()
        if watched is self.plotter.interactor and event.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            # Qt has already resolved QAction shortcuts; never forward raw letters to VTK.
            event.accept()
            return True
        if watched is self.plotter.interactor and event.type() == QEvent.Type.Wheel and self.paste.active:
            QTimer.singleShot(0, self._preview_paste)
        if watched is self.plotter.interactor and event.type() in (
                QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick,
                QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease):
            width, height = self.plotter.render_window.GetSize()
            point = event.position()
            x = round(point.x() * width / watched.width())
            y = height - round(point.y() * height / watched.height()) - 1
            modifiers = event.modifiers()
            self.molecule_pressed = bool(modifiers & self.molecule_modifier)
            if event.type() == QEvent.Type.MouseMove and event.buttons() == Qt.MouseButton.NoButton:
                self.cancel_gesture()
            self.interactor.SetEventInformation(x, y,
                int(bool(modifiers & Qt.KeyboardModifier.ControlModifier)),
                int(bool(modifiers & Qt.KeyboardModifier.ShiftModifier)))
            if event.type() == QEvent.Type.MouseMove:
                self._move(None, None)
            elif event.button() == Qt.MouseButton.LeftButton:
                if (event.type() == QEvent.Type.MouseButtonDblClick and not self.paste.active
                        and (self.structure is None or self.pick_image(x, y) is None)):
                    self.cancel_gesture()
                    self.clear_measurement()
                    self.set_selection([])
                elif event.type() == QEvent.Type.MouseButtonRelease:
                    self._release(None, None)
                else:
                    watched.setFocus()
                    self._press(None, None)
            elif event.button() == Qt.MouseButton.RightButton:
                if event.type() == QEvent.Type.MouseButtonRelease:
                    self._pan_release(None, "RightButtonReleaseEvent")
                else:
                    watched.setFocus()
                    self._pan_press(None, "RightButtonPressEvent")
            event.accept()
            return True
        return super().eventFilter(watched, event)

    def set_structure(self, structure: Structure, *, reset=False, preserve_camera_gesture=False, preserve_measurement=True):
        measurement = self.measurement_snapshot() if preserve_measurement else None
        self.charge_labels = None
        self.moment_labels = None
        self.moment_summary.hide()
        self.ghost.hide()
        self.cancel_gesture(camera=not preserve_camera_gesture)
        self.cancel_paste()
        self.structure = structure
        self.actor.VisibilityOn()
        self.metal_actor.VisibilityOn()
        self.displayed = structure.positions.copy()
        self.drag = None
        self.measurement = []
        self.measurement_text = ""
        self._update_measurement()
        numbers = np.array([atomic_numbers[symbol] for symbol in structure.symbols], dtype=int)
        self.numbers = numbers
        self.radii = self.visual.radii(structure.symbols)
        self.colors = self.visual.colors(structure.symbols)
        self._update_points(rebuild=True)
        self.restore_measurement(measurement)

        self._update_cell()
        if reset:
            self.reset_camera()
        self.plotter.render()

    def _update_cell(self):
        self.update_axis_hint()
        if self.cell_actor is not None:
            self.plotter.remove_actor(self.cell_actor, render=False)
            self.cell_actor = None
        if self.structure.cell is None:
            return
        counts = self.period_counts()
        corners = np.array(list(product((0, 1), repeat=3)))
        cell = pv.PolyData((corners * counts) @ self.structure.cell)
        edges = [[2, first, second] for first in range(8) for second in range(first + 1, 8)
                 if np.count_nonzero(corners[first] != corners[second]) == 1]
        cell.lines = np.array(edges).ravel()
        tone = self.visual.profile["scene"]["cell_color"]
        self.cell_actor = self.plotter.add_mesh(cell, color=tone, line_width=1,
                                               lighting=False, render=False, reset_camera=False)
        self.cell_actor.SetVisibility(self.visual.profile["scene"]["cell"])

    def period_counts(self):
        counts = self.periods if self.show_periodic else (1, 1, 1)
        return tuple(count if periodic else 1 for count, periodic in zip(counts, self.structure.pbc))

    def _update_points(self, *, rebuild=False):
        counts = self.period_counts()
        self.images, self.sources = periodic_images(self.displayed, self.structure.cell, counts)
        self.points.SetData(numpy_to_vtk(self.images, deep=True))
        if rebuild:
            radius = numpy_to_vtk(self.radii[self.sources], deep=True)
            radius.SetName("radius")
            self.data.GetPointData().AddArray(radius)
        self.points.Modified()
        self.data.Modified()
        symbols = tuple(self.structure.symbols[index] for index in self.sources)
        cell = self.structure.cell
        search_cell = None if cell is None else np.diag(counts) @ cell
        factor, ranges = self.visual.bond_parameters(self.bond_scale, self.bond_ranges)
        self.bonds = find_bonds(symbols, self.images, search_cell, factor, ranges)
        drawn_bonds = self.visual.filter_bonds(self.bonds, symbols, self.sources)
        vertices, edges, colors = bond_halves(self.images, drawn_bonds, self.colors[self.sources])
        if not self.visual.profile["bonds"]["split"]:
            colors[:] = color_rgb(self.visual.profile["bonds"]["color"])
        set_lines(self.bond_data, vertices, edges)
        self.bond_data.GetCellData().SetScalars(numpy_to_vtk(colors, deep=True))
        flags = self.structure.free
        free = None if flags is None else flags[self.sources].copy()
        if free is not None:
            free[~self.visual.visible(symbols, self.sources)] = True
        self.fixed.update(self.images, self.radii[self.sources], free)
        self.fixed.actor.SetVisibility(self.show_fixed)
        self._update_labels()
        self.labels.set_color(color_rgb(self.visual.profile["scene"]["label_color"]) / 255)
        self._update_colors()
        self.update_contacts()
        if self.stage is None:
            self.stage = Stage(self.plotter)
        self.stage.apply(self.visual.profile, self.images, search_cell)
        if self.background is not None:
            self.plotter.set_background(self.background)
        if self.paste.active:
            self.paste.context = self.paste_context()
            self.paste.move(self.paste.positions.mean(axis=0))
        self.force_arrows.hide()
        self._update_measurement()
        self.geometry_changed.emit()

    def set_forces(self, values, threshold, scale):
        self.force_arrows.update(self.images, self.radii[self.sources],
                                None if values is None else values[self.sources], threshold, scale)
        self.plotter.render()

    def set_periodic(self, visible, counts):
        self.cancel_gesture()
        self.show_periodic, self.periods = visible, tuple(counts)
        self.measurement = []
        self.measurement_text = ""
        self._update_points(rebuild=True)
        self._update_cell()
        self._update_measurement()
        self.plotter.reset_camera_clipping_range()
        self.plotter.render()
        self.measurement_changed.emit()

    def set_indices(self, visible, hidden):
        self.show_indices, self.hidden_elements = visible, set(hidden)
        self._update_labels()
        self.plotter.render()

    def _update_labels(self):
        symbols = tuple(self.structure.symbols[index] for index in self.sources)
        keep = self.visual.visible(symbols, self.sources)
        self.labels.update(self.images[keep], self.sources[keep], self.structure.symbols, self.hidden_elements,
                           self.show_indices, self.charge_labels, self.moment_labels)

    def set_charges(self, labels):
        if labels != self.charge_labels:
            self.charge_labels = labels
            if self.structure is not None:
                self._update_labels()
                self.plotter.render()

    def set_moment_data(self, data):
        visible = self.visual.visible(self.structure.symbols, np.arange(len(self.structure.symbols))) if data is not None else None
        self.set_moments(None if data is None else data.labels(), '' if data is None else data.summary(visible))

    def set_moments(self, labels, summary=''):
        if self.moment_summary.text() != summary:
            self.moment_summary.setText(summary)
            self.moment_summary.adjustSize()
        if self.moment_summary.isHidden() == bool(summary):
            self.moment_summary.setVisible(bool(summary))
            self.moment_summary.raise_()
        if labels != self.moment_labels:
            self.moment_labels = labels
            if self.structure is not None:
                self._update_labels()
                self.plotter.render()

    def set_fixed_visible(self, visible: bool):
        self.show_fixed = visible
        self.fixed.actor.SetVisibility(visible and self.structure is not None)
        self.plotter.render()

    def update_contacts(self):
        self.contact_rows, self.contact_missing = np.empty((0, 4)), ()
        if self.show_contacts and self.structure is not None:
            self.contact_rows, self.contact_missing = short_pairs(
                self.structure.symbols, self.displayed, self.structure.cell, self.contact_factor, pbc=self.structure.pbc)
            bad = np.zeros(len(self.structure.symbols), dtype=bool)
            bad[self.contact_rows[:, :2].astype(int).ravel()] = True
            free = np.repeat(~bad[self.sources, None], 3, axis=1)
            self.contacts.update(self.images, self.radii[self.sources] * 1.12, free)
        self.contacts.actor.SetVisibility(self.show_contacts and len(self.contact_rows) > 0)

    def set_contacts(self, visible):
        self.show_contacts = visible
        self.update_contacts()
        self.plotter.render()
        self.selection_changed.emit(self.selection)

    def camera_snapshot(self):
        camera = vtkCamera()
        camera.DeepCopy(self.plotter.camera)
        return camera

    def restore_camera(self, camera, *, fit_clipping=False):
        self.plotter.camera.DeepCopy(camera)
        # VTK DeepCopy does not invalidate the OpenGL camera's cached matrices.
        self.plotter.camera.Modified()
        self.sync_axes_projection()
        if fit_clipping:
            self.plotter.reset_camera_clipping_range()
        self.plotter.render()

    def sync_axes_projection(self):
        self.update_axis_hint()
        camera = self.plotter.renderer.axes_widget.GetRenderer().GetActiveCamera()
        camera.SetUserViewTransform(self.plotter.camera.GetUserViewTransform())
        for actor in (self.actor, self.metal_actor, self.paste.actor, self.paste.metal_actor,
                      self.ghost.preview.actor, self.ghost.preview.metal_actor):
            actor.GetShaderProperty().GetFragmentCustomUniforms().SetUniformf("viewReflection", self.mirror_sign())
        self.visual.sync(self.mirror_sign())
        self.paste.visual.sync(self.mirror_sign())
        self.ghost.preview.visual.sync(self.mirror_sign())

    def update_axis_hint(self):
        text = ""
        if self.structure is not None and self.structure.cell is not None:
            camera = self.plotter.camera
            view = np.asarray(camera.position) - camera.focal_point
            directions = self.structure.cell / np.linalg.norm(self.structure.cell, axis=1)[:, None]
            alignment = directions @ (view / np.linalg.norm(view))
            axis = int(np.argmax(np.abs(alignment)))
            if abs(alignment[axis]) > 1 - 1e-8:
                text = "abc"[axis] + ("+" if alignment[axis] > 0 else "−")
        self.axis_hint.SetInput(text)

    def set_frame(self, structure):
        same_symbols = self.structure.symbols == structure.symbols
        first, second = self.structure.cell, structure.cell
        same_cell = first is second if first is None or second is None else np.array_equal(first, second)
        if not same_symbols or self.structure.pbc != structure.pbc:
            if self.click is not None:
                x, y, _index, control = self.click
                self.click = (x, y, None, control)
            self.set_structure(structure, preserve_camera_gesture=True)
            return
        self.cancel_gesture(camera=False)
        self.structure = structure
        self.displayed = structure.positions.copy()
        self._update_points()
        if not same_cell:
            self._update_cell()
        self.plotter.reset_camera_clipping_range()
        self.plotter.render()

    def clear_structure(self):
        self.axis_hint.SetInput("")
        self.ghost.hide()
        for view in self.volume_views.values():
            view.update(None, {})
        self.force_arrows.hide()
        self.cancel_gesture()
        self.cancel_paste()
        self.structure = None
        self.actor.VisibilityOff()
        self.metal_actor.VisibilityOff()
        self.visual.hide()
        if self.stage is not None:
            self.stage.grid_actor.VisibilityOff()
        self.labels.hide()
        self.contacts.actor.VisibilityOff()
        self.contact_rows, self.contact_missing = np.empty((0, 4)), ()
        self.selection = []
        self.displayed = np.empty((0, 3))
        self.images = np.empty((0, 3))
        self.sources = np.empty(0, dtype=int)
        self.points.SetData(numpy_to_vtk(self.displayed, deep=True))
        self.data.GetPointData().Initialize()
        self.data.Modified()
        self.measurement = []
        self.measurement_text = ""
        set_lines(self.bond_data, self.displayed, np.empty((0, 2), dtype=int))
        self._update_measurement()
        self.fixed.actor.VisibilityOff()
        for actor in self.walls.actors.values():
            actor.VisibilityOff()
        self.walls.state = None
        self.water_walls.update(None)
        if self.cell_actor is not None:
            self.plotter.remove_actor(self.cell_actor, render=False)
            self.cell_actor = None
        self.plotter.render()

    def set_bond_scale(self, factor: float):
        self.bond_scale = factor
        if not self.visual.advanced:
            self.visual.profile["bonds"]["factor"] = factor
        if self.structure is not None:
            self._update_points()
            self.plotter.render()

    def set_bond_ranges(self, ranges):
        self.bond_ranges = dict(ranges)
        if not self.visual.advanced:
            self.visual.profile["bonds"]["ranges"] = dict(ranges)
        if self.structure is not None:
            self._update_points()
            self.plotter.render()

    def set_bond_radius(self, radius):
        self.bond_radius = radius
        visuals = [self.visual, self.paste.visual, self.ghost.preview.visual]
        if hasattr(self, "neb_view"):
            visuals.extend(layer.visual for layer in self.neb_view.layers)
        for visual in visuals:
            if not visual.advanced:
                value = radius * self.view_style["bonds"]["radius"] / .12
                visual.profile["bonds"]["radius"] = value
                visual.tube.SetRadius(value)
        self.plotter.render()

    def _update_measurement(self):
        points = self.images[self.measurement]
        kind = self.measurement_kind
        vertices, edges = diagram(kind, points)
        set_lines(self.measure_data, vertices, edges)
        self.measure_labels.hide()
        self.measurement_text = ""
        if not self.measurement:
            return
        size = SIZES[kind]
        order = " → ".join(str(self.sources[value] + 1) for value in self.measurement)
        if len(points) == size:
            result = measure(kind, points, self.measure_decimals)
            self.measurement_text = f"{order}：{result}"
            self.measure_labels.set_text(label_position(kind, points)[None], [result], True)
        else:
            self.measurement_text = f"{order}（{len(points)}/{size}）"

    def measurement_snapshot(self):
        if not self.measurement:
            return None
        return (self.measurement_kind, tuple(self.measurement), *self.measurement_topology)

    def restore_measurement(self, state):
        self.measurement = []
        if state is not None and state[2:] == (self.structure.symbols, self.structure.pbc, self.period_counts()):
            self.measurement_kind, indices = state[:2]
            self.measurement_topology = state[2:]
            self.measurement = list(indices)
        self._update_measurement()

    def clear_measurement(self):
        self.measurement = []
        self._update_measurement()
        self.plotter.render()
        self.measurement_changed.emit()

    def set_measure_decimals(self, decimals):
        self.measure_decimals = decimals
        self._update_measurement()

    def configure_labels(self, styles):
        self.labels.configure(styles)
        self.measure_labels.configure(styles)
        self.measure_labels.set_size(styles['measure']['size'])
        self.force_arrows.labels.pixel_ratio = self.plotter.interactor.devicePixelRatioF
        self.force_arrows.labels.configure(styles)
        self.force_arrows.labels.set_size(styles['force']['size'])

    def _update_colors(self):
        colors = self.colors[self.sources].copy()
        colors[np.isin(self.sources, self.selection)] = [255, 180, 25]
        vtk_colors = numpy_to_vtk(colors, deep=True)
        vtk_colors.SetName("color")
        self.data.GetPointData().AddArray(vtk_colors)
        symbols = tuple(self.structure.symbols[index] for index in self.sources)
        counts = self.period_counts()
        cell = None if self.structure.cell is None else np.diag(counts) @ self.structure.cell
        factor, ranges = self.visual.bond_parameters(self.bond_scale, self.bond_ranges)
        self.visual.update(self.images, symbols, self.bonds, cell, factor, ranges,
                           np.flatnonzero(np.isin(self.sources, self.selection)), pbc=self.structure.pbc, sources=self.sources)
        self.data.Modified()

    def set_look(self, kind, profile=None, advanced=False, *, rebuild=True, overrides=None, view_style=None):
        if view_style is not None:
            self.view_style = deepcopy(view_style)
        if not advanced:
            profile = deepcopy(self.view_style)
            profile["bonds"].update(radius=self.bond_radius * profile["bonds"]["radius"] / .12,
                                    factor=self.bond_scale, ranges=dict(self.bond_ranges))
            if self.bond_visibility is not None:
                profile["bonds"]["show"] = self.bond_visibility
        self.visual.configure(kind, profile, advanced, overrides)
        if advanced and self.stage is None:
            self.stage = Stage(self.plotter)
        if rebuild and self.structure is not None:
            self.radii = self.visual.radii(self.structure.symbols)
            self.colors = self.visual.colors(self.structure.symbols)
            self._update_points(rebuild=True)
            self._update_cell()
            self.plotter.reset_camera_clipping_range()
            self.plotter.render()

    def set_selection(self, indices: list[int]):
        if sorted(indices) != self.selection:
            self.ghost.hide()
        self.selection = sorted(indices)
        if self.structure is not None:
            self._update_colors()
            self.plotter.render()
        self.selection_changed.emit(self.selection)

    def cancel_gesture(self, *, camera=True):
        if camera and self.camera_drag is not None:
            if self.camera_drag[1] == "pan":
                self.style.EndPan()
            self.camera_drag = None
        self.box = None
        if camera or self.camera_drag is None:
            self.click = None
        self.rubber_band.hide()
        if self.drag is not None:
            self.drag = None
            self.ghost.hide()
            self.displayed = self.structure.positions.copy()
            self._update_points()
            self.plotter.render()

    def set_mode(self, mode: str):
        if mode == self.mode:
            return
        self.cancel_gesture()
        self.cancel_paste()
        self.mode = mode
        self.ghost.hide()
        self.plotter.render()
        cursor = Qt.CursorShape.CrossCursor if mode in ("select", "edit", *SIZES) else Qt.CursorShape.ArrowCursor
        self.plotter.interactor.setCursor(cursor)
        self.measurement_changed.emit()

    def reset_camera(self):
        self.plotter.camera.SetUserViewTransform(None)
        self.sync_axes_projection()
        self.plotter.view_isometric()
        if self.structure is not None and self.structure.cell is not None:
            center = self.structure.cell.sum(axis=0) / 2
            bounds = np.asarray(self.plotter.bounds).reshape(3, 2)
            radius = np.max(np.abs(bounds - center[:, None]), axis=1)
            self.plotter.reset_camera(bounds=(center[:, None] + radius[:, None] * [-1, 1]).ravel().tolist(), render=False)
            camera = self.plotter.camera
            camera.position = np.array(camera.position) + center - np.array(camera.focal_point)
            camera.focal_point = center
            self.plotter.reset_camera_clipping_range()
            self.update_axis_hint()
            self.plotter.render()
        else:
            self.plotter.reset_camera()
        if self.structure is not None and not self.structure.symbols and self.structure.cell is None:
            self.plotter.camera.focal_point = (0, 0, 0)
            self.plotter.camera.position = (25, 25, 25)
            self.plotter.camera.parallel_scale = 10
            self.plotter.camera.clipping_range = (0.1, 1000)
        self.update_axis_hint()

    def view_axis(self, axis, sign):
        self.cancel_gesture()
        camera = self.plotter.camera
        center = np.array(camera.focal_point)
        distance = np.linalg.norm(np.array(camera.position) - center)
        direction = self.structure.cell[axis]
        previous = np.array(camera.position) - center
        aligned = abs(np.dot(previous / distance, direction / np.linalg.norm(direction))) > 1 - 1e-10
        up = camera.up if aligned else axis_up(self.structure.cell, axis)
        camera.position = center + sign * distance * direction / np.linalg.norm(direction)
        camera.up = up
        transform = vtkTransform()
        transform.Scale(sign, 1, 1)
        camera.SetUserViewTransform(transform)
        self.sync_axes_projection()
        self.plotter.reset_camera_clipping_range()
        self.plotter.render()

    def mirror_sign(self):
        transform = self.plotter.camera.GetUserViewTransform()
        return 1 if transform is None else transform.GetMatrix().GetElement(0, 0)

    def rotate_screen(self, motion, degrees):
        self.cancel_gesture()
        self._rotate_camera(motion, degrees)
        self.plotter.reset_camera_clipping_range()
        self.plotter.render()

    def orbit_center(self):
        return (self.structure.cell.sum(axis=0) / 2 if self.structure is not None and self.structure.cell is not None
                else np.array(self.plotter.camera.focal_point))

    def _rotate_camera(self, motion, degrees, center=None):
        camera = self.plotter.camera
        center = self.orbit_center() if center is None else np.asarray(center)
        right, up = self.screen_basis()
        view = np.array(camera.position) - np.array(camera.focal_point)
        direction = {"pitch": right * self.mirror_sign(), "yaw": up * self.mirror_sign(),
                     "roll": view * self.mirror_sign()}[motion]
        camera.position = center + rotate_vector(np.array(camera.position) - center, direction, degrees)
        camera.focal_point = center + rotate_vector(np.array(camera.focal_point) - center, direction, degrees)
        camera.up = rotate_vector(np.array(camera.up), direction, degrees)
        camera.OrthogonalizeViewUp()
        self.update_axis_hint()

    def project(self, positions: np.ndarray) -> np.ndarray:
        width, height = self.plotter.render_window.GetSize()
        matrix = self.plotter.camera.GetCompositeProjectionTransformMatrix(width / height, -1, 1)
        transform = np.array([[matrix.GetElement(row, column) for column in range(4)]
                              for row in range(4)])
        clip = np.column_stack((positions, np.ones(len(positions)))) @ transform.T
        ndc = clip[:, :3] / clip[:, 3:4]
        return np.column_stack(((ndc[:, 0] + 1) * width / 2,
                                (ndc[:, 1] + 1) * height / 2, ndc[:, 2]))

    def screen_basis(self) -> tuple[np.ndarray, np.ndarray]:
        camera = self.plotter.camera
        view = np.array(camera.position) - np.array(camera.focal_point)
        view /= np.linalg.norm(view)
        right = np.cross(np.array(camera.up), view)
        right /= np.linalg.norm(right)
        return right * self.mirror_sign(), np.cross(view, right)

    def pick_image(self, x: float, y: float) -> int | None:
        screen = self.project(self.images)
        right, _up = self.screen_basis()
        edge = self.project(self.images + self.radii[self.sources, None] * right)
        radius = np.maximum(np.linalg.norm(edge[:, :2] - screen[:, :2], axis=1), 5)
        distance = np.linalg.norm(screen[:, :2] - [x, y], axis=1)
        symbols = tuple(self.structure.symbols[index] for index in self.sources)
        candidates = np.flatnonzero((distance <= radius) & (screen[:, 2] >= -1) & (screen[:, 2] <= 1)
                                   & self.visual.visible(symbols, self.sources))
        if not candidates.size:
            return None
        return int(candidates[np.argmin(screen[candidates, 2])])

    def pick(self, x: float, y: float) -> int | None:
        image = self.pick_image(x, y)
        return None if image is None else int(self.sources[image])

    def display_to_world(self, x: float, y: float, depth: float) -> np.ndarray:
        renderer = self.plotter.renderer
        renderer.SetDisplayPoint(x, y, depth)
        renderer.DisplayToWorld()
        point = np.array(renderer.GetWorldPoint())
        return point[:3] / point[3]

    def start_paste(self, symbols: tuple[str, ...], positions: np.ndarray, *, anchor=None):
        self.cancel_gesture()
        if anchor is not None:
            positions = positions - positions.mean(axis=0) + anchor
        self.paste.start(symbols, positions, self.paste_context(), pbc=self.structure.pbc)
        widget = self.plotter.interactor
        if anchor is None:
            point = widget.mapFromGlobal(QCursor.pos())
            if not widget.rect().contains(point):
                point = widget.rect().center()
            width, height = self.plotter.render_window.GetSize()
            self.interactor.SetEventPosition(round(point.x() * width / widget.width()),
                                             height - round(point.y() * height / widget.height()) - 1)
        widget.setFocus()
        self._preview_paste()
        self.paste_changed.emit()

    def paste_context(self):
        counts = self.period_counts()
        symbols = tuple(self.structure.symbols[index] for index in self.sources)
        factor, ranges = self.visual.bond_parameters(self.bond_scale, self.bond_ranges)
        return (symbols, self.images, self.structure.cell, counts, factor, ranges,
                self.visual.kind, self.visual.profile, self.visual.advanced, self.mirror_sign())

    def _preview_paste(self):
        if not self.paste.active:
            return
        renderer = self.plotter.renderer
        renderer.SetWorldPoint(*self.paste.center, 1)
        renderer.WorldToDisplay()
        depth = renderer.GetDisplayPoint()[2]
        self.paste.move(self.display_to_world(*self.interactor.GetEventPosition(), depth))
        self.plotter.render()

    def cancel_paste(self):
        if self.paste.active:
            self.paste.cancel()
            self.plotter.render()
            self.paste_changed.emit()

    def _press(self, _object, _event):
        self.cancel_gesture()
        if self.paste.active:
            self._preview_paste()
            positions = self.paste.positions.copy()
            self.cancel_paste()
            self.paste_requested.emit(positions)
            return
        x, y = self.interactor.GetEventPosition()
        if self.previewing and self.mode == "edit":
            return
        if self.mode == "rotate":
            index = None if self.structure is None else self.pick(x, y)
            self.click = (x, y, index, bool(self.interactor.GetControlKey()))
            self._camera_press("left", "rotate")
            return
        if self.structure is None:
            return
        if self.mode in ("select", "edit") and self.molecule_pressed:
            index = self.pick(x, y)
            if index is not None:
                factor, ranges = self.visual.bond_parameters(self.bond_scale, self.bond_ranges)
                group = set(connected(self.structure, index, factor, ranges))
                self.set_selection(list(group | set(self.selection) if self.interactor.GetControlKey() else group))
            return
        if self.mode == "select":
            self._box_start(x, y)
            return
        image = self.pick_image(x, y)
        index = None if image is None else int(self.sources[image])
        if self.mode in SIZES:
            if index is not None:
                self._measure_pick(image)
            else:
                self.click = (x, y, None, bool(self.interactor.GetControlKey()))
                self._camera_press("left", "rotate")
            return
        if index is None:
            self._box_start(x, y)
            return
        if self.interactor.GetControlKey():
            selected = set(self.selection)
            selected.symmetric_difference_update([index])
            self.set_selection(list(selected))
            if index not in selected:
                return
        if index not in self.selection:
            self.set_selection([index])
        renderer = self.plotter.renderer
        renderer.SetWorldPoint(*self.images[image], 1)
        renderer.WorldToDisplay()
        depth = renderer.GetDisplayPoint()[2]
        self.drag = (self.selection.copy(), self.displayed.copy(), depth,
                     self.display_to_world(x, y, depth))
        self.ghost.start(tuple(self.structure.symbols[index] for index in self.selection),
                         self.displayed[self.selection], self.paste_context(),
                         self.images[image] - self.displayed[index], pbc=self.structure.pbc)

    def _move(self, _object, _event):
        if self.camera_drag is not None:
            if self.camera_drag[1] == "rotate":
                x, y = self.interactor.GetEventPosition()
                last_x, last_y = self.camera_mouse
                self.camera_mouse = (x, y)
                width, height = self.plotter.render_window.GetSize()
                self._rotate_camera("yaw", -180 * (x - last_x) / width, self.rotation_center)
                self._rotate_camera("pitch", 180 * (y - last_y) / height, self.rotation_center)
                self.plotter.reset_camera_clipping_range()
                self.plotter.render()
            else:
                self.style.OnMouseMove()
                self.plotter.render()
            self._preview_paste()
            return
        if self.paste.active:
            self._preview_paste()
            return
        if self.box is not None:
            self._box_preview(*self.interactor.GetEventPosition())
            return
        if self.drag is None:
            return
        indices, original, depth, anchor = self.drag
        x, y = self.interactor.GetEventPosition()
        delta = self.display_to_world(x, y, depth) - anchor
        self.displayed = original.copy()
        self.displayed[indices] += delta
        self._update_points()
        self.ghost.advance(delta)
        self.plotter.render()

        if self.show_contacts:
            self.selection_changed.emit(self.selection)

    def _release(self, _object, _event):
        if self.camera_drag is not None and self.camera_drag[0] == "left":
            first_x, first_y, index, control = self.click
            x, y = self.interactor.GetEventPosition()
            self.cancel_gesture()
            if self.previewing:
                return
            if np.hypot(x - first_x, y - first_y) < 4:
                selected = set(self.selection) if control else set()
                if index is not None:
                    selected.symmetric_difference_update([index])
                self.set_selection(list(selected))
            return
        if self.box is not None:
            x, y = self.interactor.GetEventPosition()
            first_x, first_y, initial = self.box
            self.box = None
            self.rubber_band.hide()
            if np.hypot(x - first_x, y - first_y) < 4:
                index = self.pick(x, y)
                selected = set(initial)
                if index is not None:
                    selected.symmetric_difference_update([index])
            else:
                screen = self.project(self.images)
                low = np.minimum([first_x, first_y], [x, y])
                high = np.maximum([first_x, first_y], [x, y])
                inside = np.all((screen[:, :2] >= low) & (screen[:, :2] <= high), axis=1)
                inside &= (screen[:, 2] >= -1) & (screen[:, 2] <= 1)
                symbols = tuple(self.structure.symbols[index] for index in self.sources)
                inside &= self.visual.visible(symbols, self.sources)
                selected = set(initial) | set(self.sources[inside])
            self.set_selection(list(selected))
            return
        if self.drag is None:
            return
        indices, original, _depth, _anchor = self.drag
        delta = self.displayed[indices[0]] - original[indices[0]]
        self.drag = None
        if np.linalg.norm(delta) > 1e-12:
            self.move_requested.emit(indices, delta)

    def _camera_press(self, button: str, motion: str):
        self.style.SetCurrentRenderer(self.plotter.renderer)
        self.camera_drag = (button, motion)
        if motion == "rotate":
            self.camera_mouse = self.interactor.GetEventPosition()
            self.rotation_center = self.orbit_center()
        else:
            self.style.StartPan()

    def _pan_press(self, _object, event):
        self.cancel_gesture()
        self._camera_press(event.removesuffix("ButtonPressEvent").lower(), "pan")

    def _pan_release(self, _object, event):
        button = event.removesuffix("ButtonReleaseEvent").lower()
        if self.camera_drag is not None and self.camera_drag[0] == button:
            self.cancel_gesture()

    def _box_preview(self, x: int, y: int):
        first_x, first_y, _initial = self.box
        width, height = self.plotter.render_window.GetSize()
        widget = self.plotter.interactor
        scale_x, scale_y = widget.width() / width, widget.height() / height
        first = QPoint(round(first_x * scale_x), round((height - first_y) * scale_y))
        last = QPoint(round(x * scale_x), round((height - y) * scale_y))
        self.rubber_band.setGeometry(QRect(first, last).normalized())

    def _box_start(self, x: int, y: int):
        self.box = (x, y, self.selection.copy() if self.interactor.GetControlKey() else [])
        self._box_preview(x, y)
        self.rubber_band.show()

    def _measure_pick(self, index: int):
        size = SIZES[self.mode]
        if self.measurement_kind != self.mode or len(self.measurement) == size:
            self.measurement = []
        self.measurement_kind = self.mode
        if not self.measurement:
            self.measurement_topology = (self.structure.symbols, self.structure.pbc, self.period_counts())
        self.measurement.append(index)
        self.set_selection(list(set(self.sources[self.measurement])))
        self._update_measurement()
        self.plotter.render()
        self.measurement_changed.emit()

    def closeEvent(self, event):
        self.plotter.close()
        super().closeEvent(event)
