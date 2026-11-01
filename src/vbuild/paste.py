"""An uncommitted, batched atom group following the mouse in a screen plane."""

import numpy as np
from vtkmodules.util.numpy_support import numpy_to_vtk
from vtkmodules.vtkCommonCore import vtkPoints
from vtkmodules.vtkCommonDataModel import vtkPolyData
from .glyph import atom_actors, line_actor, set_lines
from .bonds import bond_halves, find_bonds
from .view import periodic_images
from .visual import Visual
from .look import color_rgb


class PastePreview:
    def __init__(self, renderer):
        self.data = vtkPolyData()
        self.points = vtkPoints()
        self.data.SetPoints(self.points)
        self.actor, self.metal_actor = atom_actors(renderer, self.data)
        self.bond_data, self.bond_actor = line_actor(renderer, (1, 1, 1), 15)
        self.bond_actor.GetMapper().SetScalarModeToUseCellData()
        self.bond_actor.GetMapper().SetColorModeToDirectScalars()
        self.visual = Visual(renderer, self.data, (self.actor, self.metal_actor), self.bond_data, self.bond_actor, preview=True)
        for actor in (self.actor, self.metal_actor, self.bond_actor):
            actor.VisibilityOff()
            actor.PickableOff()
            actor.UseBoundsOff()
        self.active = False

    def start(self, symbols, positions, context, *, pbc=None):
        self.symbols = symbols
        self.pbc = pbc
        self.context = context
        self.center = positions.mean(axis=0)
        self.relative = positions - self.center
        self.active = True
        for actor in (self.actor, self.metal_actor, self.bond_actor):
            actor.VisibilityOn()

    def move(self, center: np.ndarray):
        self.positions = self.relative + center
        host_symbols, host_points, cell, counts, factor, ranges, kind, profile, advanced, sign = self.context
        self.visual.configure(kind, profile, advanced)
        self.visual.sync(sign)
        images, sources = periodic_images(self.positions, cell, counts)
        symbols = tuple(self.symbols[index] for index in sources)
        colors = self.visual.colors(symbols)
        for name, values in (("radius", self.visual.radii(symbols)), ("color", colors)):
            array = numpy_to_vtk(values, deep=True)
            array.SetName(name)
            self.data.GetPointData().AddArray(array)
        self.points.SetData(numpy_to_vtk(images, deep=True))
        self.points.Modified()
        self.data.Modified()
        combined = np.concatenate((host_points, images))
        all_symbols = host_symbols + symbols
        search_cell = None if cell is None else np.diag(counts) @ cell
        bonds = find_bonds(all_symbols, combined, search_cell, factor, ranges)
        bonds = bonds[np.any(bonds >= len(host_points), axis=1)]
        bonds = self.visual.filter_bonds(bonds, all_symbols)
        all_colors = self.visual.colors(all_symbols)
        vertices, edges, tones = bond_halves(combined, bonds, all_colors)
        if not profile["bonds"]["split"]:
            tones[:] = color_rgb(profile["bonds"]["color"])
        set_lines(self.bond_data, vertices, edges)
        self.bond_data.GetCellData().SetScalars(numpy_to_vtk(tones, deep=True))
        self.visual.update(combined, all_symbols, bonds, search_cell, factor, ranges, [], offset=len(host_points), pbc=self.pbc)

    def cancel(self):
        self.active = False
        self.visual.hide()
        for actor in (self.actor, self.metal_actor, self.bond_actor):
            actor.VisibilityOff()
