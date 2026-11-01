"""Filter in fractional space, then transform to Cartesian, including skew cells."""

import numpy as np
import pyvista as pv


class VolumeView:
    def __init__(self, plotter):
        self.plotter = plotter
        self.actor, self.grid, self.key = None, None, None
        self.actors = []
        self.state = None

    def hide(self):
        self.state = None
        for actor in self.actors:
            self.plotter.remove_actor(actor, render=False)
        self.actors, self.actor = [], None

    def update(self, volume, options):
        state = None if volume is None else (id(volume), tuple(sorted(options.items())))
        if state == self.state and self.actor is not None:
            return
        self.hide()
        if volume is None:
            self.grid, self.key = None, None
        if volume is None or not options["enabled"] or (options["mode"] == "slice" and options["high"] <= options["low"]):
            return
        self.state = state
        self.prepare(volume, options["channel"])
        if options["mode"] == "slice":
            mesh = self.slice(volume, options)
            self.add(mesh, options, scalars="value", cmap=options["palette"], clim=(options["low"], options["high"]))
        else:
            surfaces = [(options["level"], options["color"])]
            if options.get("signed", False):
                surfaces = []
                if options["positive"]:
                    surfaces.append((abs(options["level"]), options["color"]))
                if options["negative"]:
                    level = -abs(options["level"]) if options["level_lock"] else options["negative_level"]
                    if level != 0:
                        surfaces.append((level, options["negative_color"]))
            for level, color in surfaces:
                mesh = self.grid.contour([level], scalars="value")
                self.cartesian(mesh, volume.document.structure.cell)
                self.add(mesh, options, color=color)
        self.actor = self.actors[0] if self.actors else None

    def add(self, mesh, options, **appearance):
        if not mesh.n_points:
            return
        actor = self.plotter.add_mesh(mesh, **appearance, opacity=options["opacity"], lighting=False,
                                           show_scalar_bar=False, reset_camera=False, pickable=False, render=False)
        actor.UseBoundsOff()
        self.actors.append(actor)

    def prepare(self, volume, channel):
        key = (id(volume), channel)
        if key != self.key:
            data = volume.channels[channel]
            self.grid = pv.ImageData(dimensions=np.array(data.shape) + 1, spacing=1 / np.array(data.shape))
            self.grid.point_data["value"] = np.pad(data, ((0, 1),) * 3, mode="wrap").ravel(order="F")
            self.key = key

    def slice(self, volume, options):
        self.prepare(volume, options["channel"])
        cell = volume.document.structure.cell
        axis, position = options["axis"], options["position"]
        normal, origin = np.zeros(3), np.zeros(3)
        if axis < 3:
            normal[axis], origin[axis] = 1, position
        else:
            direction = axis - 3
            normal = cell[:, direction]
            point = np.zeros(3)
            point[direction] = position
            origin = np.linalg.solve(cell.T, point)
        mesh = self.grid.slice(normal=normal, origin=origin)
        self.cartesian(mesh, cell)
        return mesh

    @staticmethod
    def cartesian(mesh, cell):
        transform = np.eye(4)
        transform[:3, :3] = cell.T
        mesh.transform(transform, inplace=True)
