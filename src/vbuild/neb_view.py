"""Reuse batched atom/bond previews for independently translucent NEB images."""

import numpy as np

from .paste import PastePreview
from .visual import reflected_normals, reflection


class NebView:
    def __init__(self, scene):
        self.scene, self.layers, self.enabled, self.key = scene, [], False, None
        self.image_keys = {}
        scene.plotter.renderer.AddObserver("StartEvent", self.before_render)

    def before_render(self, *_args):
        if not self.enabled:
            return
        visual = self.scene.visual
        for actor in visual.actors():
            actor.VisibilityOff()
        for layer in self.layers:
            layer.visual.sync(visual.sign)
            for actor in (layer.actor, layer.metal_actor):
                reflection(actor, visual.sign)

    def hide(self):
        self.enabled = False
        self.key = None
        self.image_keys.clear()
        for layer in self.layers:
            layer.cancel()

    def update(self, run, index):
        self.enabled = True
        renderer, scene = self.scene.plotter.renderer, self.scene
        common = (id(run), scene.period_counts(), scene.visual.kind, repr(scene.visual.profile), scene.visual.advanced,
                  scene.bond_scale, repr(scene.bond_ranges))
        key = (index, tuple(sorted(run.selected)), tuple(sorted(run.opaque)), run.opacity, run.bond_opacity, common)
        if key == self.key:
            return
        self.key = key
        while len(self.layers) < len(run.images):
            layer = PastePreview(renderer)
            for actor in (layer.actor, layer.metal_actor):
                reflected_normals(actor)
            self.layers.append(layer)
        for image, layer in enumerate(self.layers):
            if image >= len(run.images) or image not in run.selected:
                layer.cancel()
                self.image_keys.pop(image, None)
                continue
            geometry = (common, min(index, run.images[image].count-1))
            if geometry != self.image_keys.get(image):
                structure = run.at(index)[image].structure
                visual = scene.visual
                counts = scene.period_counts()
                factor, ranges = visual.bond_parameters(scene.bond_scale, scene.bond_ranges)
                context = ((), np.empty((0, 3)), structure.cell, counts, factor, ranges,
                           visual.kind, visual.profile, visual.advanced, visual.sign)
                layer.start(structure.symbols, structure.positions, context, pbc=structure.pbc)
                layer.move(structure.positions.mean(axis=0))
                self.image_keys[image] = geometry
            opacity = 1 if image in run.opaque else run.opacity
            for actor in layer.visual.atom_actors():
                actor.GetProperty().SetOpacity(opacity)
            for actor in (layer.bond_actor, layer.visual.tube_actor):
                actor.GetProperty().SetOpacity(1 if image in run.opaque else run.bond_opacity)
