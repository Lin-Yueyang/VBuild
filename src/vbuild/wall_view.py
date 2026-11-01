"""Non-pickable translucent Cartesian z planes; never part of the structure."""

from itertools import product

import numpy as np
from vtkmodules.vtkFiltersSources import vtkPlaneSource
from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper


class WallPreview:
    def __init__(self, renderer):
        self.renderer, self.state = renderer, None
        self.planes, self.actors = {}, {}
        for name, color in (("upper", (0.25, 0.55, 0.80)), ("lower", (0.85, 0.55, 0.30))):
            plane = vtkPlaneSource()
            mapper = vtkPolyDataMapper()
            mapper.SetInputConnection(plane.GetOutputPort())
            actor = vtkActor()
            actor.SetMapper(mapper)
            actor.GetProperty().SetColor(*color)
            actor.GetProperty().SetOpacity(0.12)
            actor.GetProperty().LightingOff()
            actor.PickableOff()
            actor.UseBoundsOff()
            actor.VisibilityOff()
            renderer.AddActor(actor)
            self.planes[name], self.actors[name] = plane, actor
        renderer.AddObserver("StartEvent", self.clipping)

    def clipping(self, _renderer, _event):
        visible = [actor for actor in self.actors.values() if actor.GetVisibility()]
        if visible:
            bounds = np.array([self.renderer.ComputeVisiblePropBounds(),
                               *(actor.GetBounds() for actor in visible)]).reshape(-1, 3, 2)
            combined = np.column_stack((bounds[:, :, 0].min(axis=0), bounds[:, :, 1].max(axis=0)))
            self.renderer.ResetCameraClippingRange(combined.ravel())

    def update(self, structure, walls, visible, periods):
        state = None
        if visible and structure is not None:
            points = structure.positions
            if structure.cell is not None:
                corners = np.array(list(product((0, 1), repeat=3))) * periods
                points = np.vstack((points, corners @ structure.cell))
            low, high = points[:, :2].min(axis=0) - 1, points[:, :2].max(axis=0) + 1
            state = (walls, tuple(low), tuple(high))
        if self.state == state:
            return False
        self.state = state
        for actor in self.actors.values():
            actor.VisibilityOff()
        if state is None:
            return True
        for name, plane in self.planes.items():
            wall = getattr(walls, name)
            if wall.enabled:
                plane.SetOrigin(low[0], low[1], wall.position)
                plane.SetPoint1(high[0], low[1], wall.position)
                plane.SetPoint2(low[0], high[1], wall.position)
                self.actors[name].VisibilityOn()
        return True


class WaterRange(WallPreview):
    """Two exact cell cross-sections, including oblique a/b and tilted c."""

    def update(self, region):
        for actor in self.actors.values():
            actor.VisibilityOff()
        if region is None:
            return
        for name, height in (("lower", region.lower), ("upper", region.upper)):
            origin = region.cell[2] * (height / region.cell[2, 2])
            plane = self.planes[name]
            plane.SetOrigin(*origin)
            plane.SetPoint1(*(origin + region.cell[0]))
            plane.SetPoint2(*(origin + region.cell[1]))
            self.actors[name].VisibilityOn()
