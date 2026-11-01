"""Batched force arrows anchored to atom surfaces, with magnitude labels."""

import numpy as np
from vtkmodules.util.numpy_support import numpy_to_vtk
from vtkmodules.vtkCommonCore import vtkPoints
from vtkmodules.vtkCommonDataModel import vtkPolyData
from vtkmodules.vtkFiltersSources import vtkArrowSource
from vtkmodules.vtkRenderingCore import vtkActor, vtkGlyph3DMapper

from .display import AtomLabels


class ForceArrows:
    def __init__(self, renderer):
        self.data, self.points = vtkPolyData(), vtkPoints()
        self.data.SetPoints(self.points)
        arrow = vtkArrowSource()
        arrow.SetTipResolution(16)
        arrow.SetShaftResolution(12)
        arrow.SetTipLength(.25)
        mapper = vtkGlyph3DMapper()
        mapper.SetInputData(self.data)
        mapper.SetSourceConnection(arrow.GetOutputPort())
        mapper.SetOrientationArray("direction")
        mapper.SetOrientationModeToDirection()
        mapper.SetScaleArray("size")
        mapper.SetScaleModeToScaleByVectorComponents()
        mapper.ScalarVisibilityOff()
        self.actor = vtkActor()
        self.actor.SetMapper(mapper)
        self.actor.GetProperty().SetColor(1, .3, .05)
        self.actor.GetProperty().LightingOff()
        self.actor.PickableOff()
        self.actor.UseBoundsOff()
        self.actor.VisibilityOff()
        renderer.AddActor(self.actor)
        self.labels = AtomLabels(renderer, kind='force')
        self.indices = np.empty(0, dtype=int)
        self.vectors = np.empty((0, 3))

    def hide(self):
        self.actor.VisibilityOff()
        self.labels.hide()
        self.indices = np.empty(0, dtype=int)

    def update(self, positions, radii, forces, threshold, scale):
        if forces is None:
            self.hide()
            return
        magnitude = np.linalg.norm(forces, axis=1)
        self.indices = np.flatnonzero(magnitude > threshold)
        self.vectors = forces[self.indices].copy()
        value = magnitude[self.indices]
        direction = self.vectors / value[:, None]
        starts = positions[self.indices] + direction * radii[self.indices, None]
        length = value * scale
        self.points.SetData(numpy_to_vtk(starts, deep=True))
        for name, data in (("direction", direction), ("size", np.column_stack((length, np.ones((len(value), 2)))))):
            array = numpy_to_vtk(data, deep=True)
            array.SetName(name)
            self.data.GetPointData().AddArray(array)
        self.points.Modified()
        self.data.Modified()
        self.actor.SetVisibility(bool(len(value)))
        tips = starts + direction * length[:, None]
        self.labels.set_text(tips, [f"{force:.2f} eV/Å" for force in value], bool(len(value)))
