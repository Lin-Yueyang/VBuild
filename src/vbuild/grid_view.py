"""Batched cell/Cartesian rulers, grids and independently translucent planes."""

from copy import deepcopy
import numpy as np
from vtkmodules.vtkCommonCore import vtkPoints
from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkPolyData
from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper
from vtkmodules.util.numpy_support import numpy_to_vtk, vtk_to_numpy

from .glyph import line_actor, set_lines
from .grid import coordinate_frame, geometry
from .label_view import AtomLabels

COLORS = ((.65, .22, .20), (.18, .47, .30), (.23, .37, .65))


class RulerLabels(AtomLabels):
    def project(self, *args):
        super().project(*args)
        if not self.actor.GetVisibility() or not len(self.owners):
            return
        points = []
        for position in (*self.line, self.center):
            self.renderer.SetWorldPoint(*position, 1)
            self.renderer.WorldToDisplay()
            points.append(np.array(self.renderer.GetDisplayPoint()[:2]))
        start, end, center = points
        delta = end - start
        length = np.linalg.norm(delta)
        direction = delta / length if length > 1e-6 else np.array([0., 1.])
        normal = np.array([-direction[1], direction[0]])
        if np.dot(normal, (start + end) / 2 - center) < 0:
            normal *= -1
        shift = (direction * 2.4 + normal * .25) * self.size * self.ratio
        vertices = vtk_to_numpy(self.geometry.GetPoints().GetData())
        vertices[np.repeat(self.owners == len(self.texts) - 1, 4), :2] += shift
        self.screen_centers[-1] += shift
        self.geometry.GetPoints().Modified()


class CoordinateGrid:
    def __init__(self, plotter):
        self.plotter, self.key = plotter, None
        self.axes, self.grids, self.planes, self.labels = [], [], [], []
        self.actors = []
        for color in COLORS:
            axis = line_actor(plotter.renderer, color, 1.5)
            grid = line_actor(plotter.renderer, color, 1)
            data, actor = vtkPolyData(), vtkActor()
            mapper = vtkPolyDataMapper()
            mapper.SetInputData(data)
            actor.SetMapper(mapper)
            actor.GetProperty().SetColor(*color)
            actor.GetProperty().LightingOff()
            plotter.renderer.AddActor(actor)
            labels = RulerLabels(plotter.renderer, pixel_ratio=plotter.interactor.devicePixelRatioF, kind='grid')
            labels.set_color(color)
            labels.configure({'grid': dict(bold=False, width=1.)})
            self.axes.append(axis)
            self.grids.append(grid)
            self.planes.append((data, actor))
            self.labels.append(labels)
            for prop in (axis[1], grid[1], actor, labels.actor):
                prop.PickableOff()
                prop.UseBoundsOff()
                prop.VisibilityOff()
                self.actors.append(prop)

    def hide(self):
        self.key = None
        for actor in self.actors:
            actor.VisibilityOff()

    def update(self, structure, options, periods=(1, 1, 1)):
        if structure is None or not options['show']:
            self.hide()
            return
        limits, basis = coordinate_frame(structure, periods, options['basis'])
        if limits is None:
            self.hide()
            return
        key = (limits.tolist(), basis.tolist(), deepcopy(options))
        if key == self.key:
            return
        rows = geometry(limits, options, basis)
        self.key = key
        for index, row in enumerate(rows):
            for group, points, visible, opacity in (
                    (self.axes, row['lines'], options['axes'][index], options['axis_opacity']),
                    (self.grids, row['grid'], options['grids'][index], options['grid_opacity'])):
                data, actor = group[index]
                set_lines(data, points.reshape(-1, 3), np.arange(len(points) * 2).reshape(-1, 2))
                actor.SetVisibility(visible)
                actor.GetProperty().SetLineWidth(options['width'])
                actor.GetProperty().SetOpacity(opacity)
            data, actor = self.planes[index]
            points = vtkPoints()
            points.SetData(numpy_to_vtk(row['plane'], deep=True))
            data.SetPoints(points)
            cells = vtkCellArray()
            cells.InsertNextCell(4, [0, 1, 2, 3])
            data.SetPolys(cells)
            data.Modified()
            actor.SetVisibility(options['planes'][index])
            actor.GetProperty().SetOpacity(options['plane_opacity'])
            self.labels[index].set_size(options['font'])
            self.labels[index].line, self.labels[index].center = row['lines'][0], limits.mean(axis=0) @ basis
            self.labels[index].set_text(row['positions'], row['labels'], options['axes'][index])
