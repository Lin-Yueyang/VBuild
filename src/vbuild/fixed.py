"""Batched wireframe markers around atoms with at least one fixed direction."""

import numpy as np
from vtkmodules.util.numpy_support import numpy_to_vtk
from vtkmodules.vtkCommonCore import vtkPoints
from vtkmodules.vtkCommonDataModel import vtkPolyData
from vtkmodules.vtkFiltersSources import vtkSphereSource
from vtkmodules.vtkRenderingCore import vtkActor, vtkGlyph3DMapper


class FixedMarkers:
    def __init__(self, renderer):
        self.data = vtkPolyData()
        self.points = vtkPoints()
        self.data.SetPoints(self.points)
        sphere = vtkSphereSource()
        sphere.SetRadius(1)
        sphere.SetThetaResolution(16)
        sphere.SetPhiResolution(8)
        mapper = vtkGlyph3DMapper()
        mapper.SetInputData(self.data)
        mapper.SetSourceConnection(sphere.GetOutputPort())
        mapper.SetScaleArray("radius")
        mapper.SetScaleModeToScaleByMagnitude()
        mapper.OrientOff()
        mapper.ScalarVisibilityOff()
        self.actor = vtkActor()
        self.actor.SetMapper(mapper)
        self.actor.GetProperty().SetRepresentationToWireframe()
        self.actor.GetProperty().SetColor(0.08, 0.55, 0.82)
        self.actor.GetProperty().SetLineWidth(1.5)
        self.actor.GetProperty().LightingOff()
        self.actor.VisibilityOff()
        self.actor.PickableOff()
        renderer.AddActor(self.actor)

    def update(self, positions, radii, free):
        mask = np.zeros(len(positions), dtype=bool) if free is None else np.any(~free, axis=1)
        self.points.SetData(numpy_to_vtk(positions[mask], deep=True))
        radius = numpy_to_vtk(radii[mask] * 1.12, deep=True)
        radius.SetName("radius")
        self.data.GetPointData().AddArray(radius)
        self.points.Modified()
        self.data.Modified()
