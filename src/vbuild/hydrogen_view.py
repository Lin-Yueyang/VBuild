"""One VTK line actor for a structure's hydrogen bonds and displayed replicas."""

import numpy as np

from .glyph import line_actor, set_lines
from .hydrogen import dash_vertices
from .view import periodic_images


class HydrogenBonds:
    def __init__(self, renderer):
        self.data, self.actor = line_actor(renderer, (.32, .59, .70), 2)
        self.actor.PickableOff()
        self.actor.UseBoundsOff()
        self.actor.VisibilityOff()

    def update(self, enabled, structure, positions, counts, visual):
        self.actor.VisibilityOff()
        if not enabled or "H" not in structure.symbols:
            return
        vertices = dash_vertices(structure.symbols, positions, structure.cell, structure.pbc,
                                 visual.visible(structure.symbols))
        if len(vertices):
            vertices, _ = periodic_images(vertices, structure.cell, counts)
            set_lines(self.data, vertices, np.arange(len(vertices)).reshape(-1, 2))
            self.actor.VisibilityOn()
