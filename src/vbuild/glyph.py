"""Identical atom geometry and materials for structures and placement previews."""

import numpy as np
from vtkmodules.util.numpy_support import numpy_to_vtk, numpy_to_vtkIdTypeArray
from vtkmodules.vtkCommonCore import vtkPoints
from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkPolyData
from vtkmodules.vtkFiltersSources import vtkSphereSource
from vtkmodules.vtkRenderingCore import vtkActor, vtkGlyph3DMapper, vtkPolyDataMapper


def line_actor(renderer, color, width):
    data = vtkPolyData()
    mapper = vtkPolyDataMapper()
    mapper.SetInputData(data)
    mapper.SetResolveCoincidentTopologyToPolygonOffset()
    mapper.SetRelativeCoincidentTopologyLineOffsetParameters(0, -2)
    actor = vtkActor()
    actor.SetMapper(mapper)
    actor.GetProperty().SetColor(*color)
    actor.GetProperty().SetLineWidth(width)
    actor.GetProperty().LightingOff()
    renderer.AddActor(actor)
    return data, actor


def set_lines(data, vertices, edges):
    points = vtkPoints()
    points.SetData(numpy_to_vtk(vertices, deep=True))
    lines = vtkCellArray()
    cells = np.column_stack((np.full(len(edges), 2), edges)).astype(np.int64).ravel()
    lines.SetCells(len(edges), numpy_to_vtkIdTypeArray(cells, deep=True))
    data.SetPoints(points)
    data.SetLines(lines)
    data.Modified()


def atom_actors(renderer, data):
    sphere = vtkSphereSource()
    sphere.SetRadius(1.0)
    sphere.SetThetaResolution(20)
    sphere.SetPhiResolution(16)
    actors = []
    for name in ("plain", "metal"):
        mapper = vtkGlyph3DMapper()
        mapper.SetInputData(data)
        mapper.SetSourceConnection(sphere.GetOutputPort())
        mapper.SetScaleArray("radius")
        mapper.SetScaleModeToScaleByMagnitude()
        mapper.OrientOff()
        mapper.SetScalarModeToUsePointFieldData()
        mapper.SelectColorArray("color")
        mapper.SetColorModeToDirectScalars()
        mapper.MaskingOn()
        mapper.SetMaskArray(name)
        actor = vtkActor()
        actor.SetMapper(mapper)
        material = actor.GetProperty()
        material.SetAmbient(0.35)
        material.SetDiffuse(0.65)
        if name == "metal":
            material.SetSpecular(0.28)
            material.SetSpecularPower(28)
            material.SetInterpolationToPhong()
        renderer.AddActor(actor)
        actors.append(actor)
    return actors
