"""Batched representation geometry and per-element PBR materials."""

import numpy as np
from vtkmodules.util.numpy_support import numpy_to_vtk, numpy_to_vtkIdTypeArray
from vtkmodules.vtkCommonCore import vtkBitArray, vtkPoints
from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkPolyData
from vtkmodules.vtkFiltersCore import vtkTubeFilter
from vtkmodules.vtkFiltersSources import vtkSphereSource, vtkRegularPolygonSource
from vtkmodules.vtkRenderingCore import vtkActor, vtkGlyph3DMapper, vtkPolyDataMapper, vtkLight

from .glyph import line_actor, set_lines
from .look import color_rgb, default_style
from .poly import coordination
from .appearance import entries, model, shown, visible_bonds
from .surface import Outlines, background_bonds, shading


def reflection(actor, sign):
    actor.GetShaderProperty().GetFragmentCustomUniforms().SetUniformf("viewReflection", sign)


def reflected_normals(actor):
    actor.GetShaderProperty().AddFragmentShaderReplacement("//VTK::Normal::Impl", True,
        "//VTK::Normal::Impl\nnormalVCVSOutput *= viewReflection;\n", False)
    reflection(actor, 1)


def mask(data, name, values):
    bits = vtkBitArray()
    bits.SetName(name)
    for value in values:
        bits.InsertNextValue(bool(value))
    data.GetPointData().AddArray(bits)


def material(prop, values, scene, *, specular=0.5):
    prop.SetMetallic(values["metallic"])
    prop.SetRoughness(values["roughness"])
    prop.SetOpacity(values["opacity"])
    soft = scene["lighting"] != "studio"
    prop.SetAmbient(scene["ambient"] if soft else 0)
    prop.SetDiffuse(1 - scene["ambient"] if soft else 1)
    prop.SetSpecular(0 if soft else specular)
    prop.SetCoatStrength(0 if soft else specular)
    prop.SetCoatRoughness(0.1)
    if soft:
        prop.SetInterpolationToPhong()
    else:
        prop.SetInterpolationToPBR()


class Visual:
    def __init__(self, renderer, data, atoms, bond_data, bond_actor, *, preview=False):
        self.renderer, self.data, self.atoms = renderer, data, atoms
        self.bond_actor = bond_actor
        self.preview = preview
        self.kind, self.advanced, self.profile = "ball", False, default_style()
        self.elements = {}
        self.light_modes = {}
        self.bond_layer = None
        self.overrides = {}
        self.sign = 1
        self.sphere = vtkSphereSource()
        self.sphere.SetRadius(1)
        self.sphere.SetThetaResolution(48)
        self.sphere.SetPhiResolution(24)
        self.disk = vtkRegularPolygonSource()
        self.disk.SetRadius(1)
        self.disk.SetNumberOfSides(96)
        self.disk.GeneratePolygonOn()
        self.disk.GeneratePolylineOff()
        self.outlines = Outlines(renderer, data)
        renderer.AddObserver("StartEvent", self.face_camera)
        self.tube = vtkTubeFilter()
        self.tube.SetInputData(bond_data)
        self.tube.SetNumberOfSides(16)
        self.tube.CappingOn()
        mapper = vtkPolyDataMapper()
        mapper.SetInputConnection(self.tube.GetOutputPort())
        mapper.SetScalarModeToUseCellData()
        mapper.SetColorModeToDirectScalars()
        self.tube_actor = vtkActor()
        self.tube_actor.SetMapper(mapper)
        self.tube_actor.GetProperty().SetInterpolationToPhong()
        renderer.AddActor(self.tube_actor)
        self.poly_data = vtkPolyData()
        mapper = vtkPolyDataMapper()
        mapper.SetInputData(self.poly_data)
        mapper.SetScalarModeToUseCellData()
        mapper.SetColorModeToDirectScalars()
        self.poly_actor = vtkActor()
        self.poly_actor.SetMapper(mapper)
        renderer.AddActor(self.poly_actor)
        for actor in (self.tube_actor, self.poly_actor):
            reflected_normals(actor)
            actor.VisibilityOff()
            if preview:
                actor.PickableOff()
                actor.UseBoundsOff()

    def configure(self, kind, profile, advanced, overrides=None):
        self.kind, self.advanced = kind, advanced
        self.overrides = {} if overrides is None else overrides
        self.profile = default_style() if profile is None else profile
        quality = self.profile["scene"]["quality"] if advanced else min(48, self.profile["scene"]["quality"])
        self.sphere.SetThetaResolution(quality)
        self.sphere.SetPhiResolution(quality if advanced else min(24, quality))
        self.tube.SetNumberOfSides(quality // 2 if advanced else 16)
        self.bond_actor.SetPosition(0, 0, 0)
        disc = self.profile["scene"]["lighting"] == "disc"
        if disc != self.bond_layer:
            background_bonds(self.bond_actor, disc)
            self.bond_layer = disc

    def source(self):
        return (self.disk if self.profile["scene"]["lighting"] == "disc" else self.sphere).GetOutputPort()

    def face_camera(self, *_args):
        if self.profile["scene"]["lighting"] == "disc":
            normal = np.array(self.renderer.GetActiveCamera().GetViewPlaneNormal())
            self.disk.SetNormal(*normal)
            self.bond_actor.SetPosition(*(-.01 * normal))
            for actor in self.outlines.actors.values():
                actor.SetPosition(*(-.005 * normal))

    def atom_actors(self):
        return (*self.atoms, self.poly_actor, *self.elements.values(), *self.outlines.actors.values())

    def actors(self):
        return (*self.atom_actors(), self.bond_actor, self.tube_actor)

    def radii(self, symbols):
        values = entries(self.profile, symbols, self.overrides)
        return np.array([entry["filling" if model(entry, self.kind) == "space" else "radius"] for entry in values]) * self.profile["scene"]["atom_scale"]

    def colors(self, symbols):
        return np.array([color_rgb(entry["color"]) for entry in entries(self.profile, symbols, self.overrides)], dtype=np.uint8).reshape(-1, 3)

    def bond_parameters(self, factor, ranges):
        return (self.profile["bonds"]["factor"], self.profile["bonds"]["ranges"]) if self.advanced else (factor, ranges)

    def visible(self, symbols, sources=None):
        return shown(entries(self.profile, symbols, self.overrides, sources))

    def filter_bonds(self, bonds, symbols, sources=None):
        return visible_bonds(bonds, entries(self.profile, symbols, self.overrides, sources), self.kind)

    def element_actor(self, key):
        if key not in self.elements:
            mapper = vtkGlyph3DMapper()
            mapper.SetInputData(self.data)
            mapper.SetSourceConnection(self.source())
            mapper.SetScaleArray("radius")
            mapper.SetScaleModeToScaleByMagnitude()
            mapper.OrientOff()
            mapper.SetScalarModeToUsePointFieldData()
            mapper.SelectColorArray("color")
            mapper.SetColorModeToDirectScalars()
            mapper.MaskingOn()
            mapper.SetMaskArray("element_" + key)
            actor = vtkActor()
            actor.SetMapper(mapper)
            reflected_normals(actor)
            reflection(actor, self.sign)
            if self.preview:
                actor.PickableOff()
                actor.UseBoundsOff()
            self.renderer.AddActor(actor)
            self.elements[key] = actor
        self.elements[key].GetMapper().SetSourceConnection(self.source())
        return self.elements[key]

    def illuminate(self, actor, values, *, specular=0.5):
        scene = self.profile["scene"]
        material(actor.GetProperty(), values, scene, specular=specular)
        if self.light_modes.get(actor) != scene["lighting"]:
            shading(actor, scene["lighting"])
            self.light_modes[actor] = scene["lighting"]

    def sync(self, sign):
        self.sign = sign
        for actor in (*self.elements.values(), self.tube_actor, self.poly_actor):
            reflection(actor, sign)
        self.outlines.sync(sign, self.profile["scene"]["lighting"] == "disc")

    def update(self, points, symbols, bonds, cell, factor, ranges, selected, *, offset=0, pbc=None, sources=None):
        bonds = self.filter_bonds(bonds, symbols, sources)
        self.outlines.hide()
        local_symbols = symbols[offset:]
        values = entries(self.profile, symbols, self.overrides, sources)
        kinds = np.array([model(entry, self.kind) for entry in values])
        visible = np.ones(len(symbols), dtype=bool)
        bonded = np.unique(bonds)
        visible[bonded] = kinds[bonded] != "stick"
        visible = visible[offset:]
        visible[selected] = True
        visible &= shown(values)[offset:]
        indices = np.arange(len(symbols)) if sources is None else sources
        groups = np.array(["atom_" + str(index) if index in self.overrides else symbol
                           for symbol, index in zip(local_symbols, indices[offset:])])
        if not self.advanced:
            # Colors and radii are per-point; identical materials share one GPU batch.
            materials = [(entry["opacity"], entry["metallic"], entry["roughness"], entry["specular"])
                         for entry in values[offset:]]
            unique = {entry: str(index) for index, entry in enumerate(dict.fromkeys(materials))}
            groups = np.array(["material_" + unique[entry] for entry in materials])
        active = set(groups)
        for key in active:
            selected_group = groups == key
            entry = values[offset + int(np.flatnonzero(selected_group)[0])]
            actor = self.element_actor(key)
            reflection(actor, self.sign)
            mask(self.data, "element_" + key, selected_group & visible)
            self.illuminate(actor, entry, specular=entry["specular"])
            lighting = self.profile["scene"]["lighting"]
            if lighting in ("toon", "disc"):
                self.outlines.update(key, self.source(), entry["opacity"], lighting == "disc", self.sign)
        for key, actor in self.elements.items():
            actor.SetVisibility(key in active)
        for actor in self.atoms:
            actor.VisibilityOff()
        bond = self.profile["bonds"]
        show_bonds = bond["show"]
        cylinder = bond["shape"] == "cylinder"
        self.bond_actor.SetVisibility(show_bonds and not cylinder)
        self.bond_actor.GetProperty().SetLineWidth(bond["width"])
        self.bond_actor.GetProperty().SetOpacity(bond["opacity"])
        self.tube.SetRadius(bond["radius"])
        self.tube_actor.SetVisibility(show_bonds and cylinder)
        reflection(self.tube_actor, self.sign)
        self.tube_actor.GetProperty().LightingOn()
        self.illuminate(self.tube_actor, bond)
        poly = self.profile["polyhedra"]
        show_poly = poly["show"] if self.advanced else self.kind == "poly"
        self.poly_actor.SetVisibility(show_poly)
        reflection(self.poly_actor, self.sign)
        if show_poly:
            keep = shown(values)
            indices = np.flatnonzero(keep)
            vertices, faces, owners = coordination(tuple(symbols[index] for index in indices), points[keep], cell, factor, ranges, poly["centers"], poly["neighbors"], pbc=pbc)
            owners = indices[owners]
            keep_faces = owners >= offset
            if self.advanced and np.any(kinds == "poly"):
                keep_faces &= kinds[owners] == "poly"
            faces, owners = faces[keep_faces], owners[keep_faces]
            positions = vtkPoints()
            positions.SetData(numpy_to_vtk(vertices, deep=True))
            polygons = vtkCellArray()
            polygons.SetCells(len(faces), numpy_to_vtkIdTypeArray(np.column_stack((np.full(len(faces), 3), faces)).astype(np.int64).ravel(), deep=True))
            self.poly_data.SetPoints(positions)
            self.poly_data.SetPolys(polygons)
            tones = np.array([color_rgb(entry["color"]) for entry in values], dtype=np.uint8).reshape(-1, 3)
            self.poly_data.GetCellData().SetScalars(numpy_to_vtk(tones[owners], deep=True))
            self.poly_data.Modified()
            self.poly_actor.GetProperty().SetOpacity(poly["opacity"])
            self.poly_actor.GetProperty().SetEdgeVisibility(poly["edges"])
            self.poly_actor.GetProperty().SetEdgeColor(0.35, 0.4, 0.5)
            self.poly_actor.GetProperty().SetAmbient(0.5 if self.advanced and self.profile["scene"]["lighting"] == "soft" else 0)
            self.poly_actor.GetProperty().SetSpecular(0)

    def hide(self):
        for actor in self.actors():
            actor.VisibilityOff()


class Stage:
    def __init__(self, plotter):
        self.plotter = plotter
        self.light_key = None
        self.grid_data, self.grid_actor = line_actor(plotter.renderer, (0.22, 0.22, 0.22), 1)
        self.grid_actor.VisibilityOff()
        self.grid_actor.PickableOff()
        self.grid_actor.UseBoundsOff()

    def apply(self, profile, points, cell, *, axes_visible=True):
        renderer = self.plotter.renderer
        scene = profile["scene"]
        self.plotter.set_background(scene["background"])
        key = tuple(scene[name] for name in ("lighting", "key", "fill", "rim"))
        if key != self.light_key:
            self.light_key = key
            renderer.RemoveAllLights()
            for name, position in (("key", (-0.6, 0.8, 1)), ("fill", (1, -0.3, 0.5)), ("rim", (0.5, 0.6, -1))):
                light = vtkLight()
                light.SetLightTypeToCameraLight()
                light.SetPosition(*position)
                light.SetFocalPoint(0, 0, 0)
                light.PositionalOff()
                light.SetIntensity(scene[name] * (0.65 if scene["lighting"] == "soft" else 1))
                renderer.AddLight(light)
        renderer.axes_widget.SetEnabled(axes_visible and scene["axes"])
        self.plotter.camera.parallel_projection = scene["parallel"]
        self.grid_actor.SetVisibility(scene["grid"])
        if not scene["grid"]:
            return
        self.grid_actor.GetProperty().SetColor(*(color_rgb(scene["grid_color"]) / 255))
        bounds = points
        if cell is not None:
            from itertools import product
            bounds = np.vstack((points, np.array(list(product((0, 1), repeat=3))) @ cell))
        if not len(bounds):
            bounds = np.array([[0, 0, 0], [15, 15, 15]])
        low, high = bounds.min(axis=0), bounds.max(axis=0)
        step = scene["grid_step"]
        low, high = np.floor(low / step) * step - step, np.ceil(high / step) * step + step
        vertices, edges = [], []
        for axis in (0, 1):
            for value in np.arange(low[axis], high[axis] + step / 2, step):
                a, b = low.copy(), high.copy()
                a[axis] = b[axis] = value
                a[2] = b[2] = low[2] - 0.5
                edges.append([len(vertices), len(vertices) + 1])
                vertices.extend((a, b))
        set_lines(self.grid_data, np.asarray(vertices), np.asarray(edges))
