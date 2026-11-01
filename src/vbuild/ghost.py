"""Unpickable movement reference, shared representation and Cartesian displacement."""

import numpy as np

from .display import AtomLabels
from .glyph import line_actor, set_lines
from .paste import PastePreview


class MoveGhost:
    def __init__(self, renderer):
        self.preview = PastePreview(renderer)
        self.line_data, self.line_actor = line_actor(renderer, (.4, .45, .5), 2)
        self.line_actor.PickableOff()
        self.line_actor.UseBoundsOff()
        self.labels = AtomLabels(renderer)
        self.enabled, self.opacity, self.decimals = True, .2, 2
        self.active, self.prepared = False, False
        self.shift, self.delta = np.zeros(3), np.zeros(3)
        self.hide()

    def configure(self, enabled, opacity, decimals):
        self.enabled, self.opacity, self.decimals = enabled, opacity, decimals
        if not enabled:
            self.hide()
        elif self.prepared:
            self.advance(self.delta)

    def hide(self):
        self.active, self.prepared = False, False
        self.preview.cancel()
        self.line_actor.VisibilityOff()
        self.labels.hide()

    def start(self, symbols, positions, context, shift=None, *, pbc=None):
        self.hide()
        if not self.enabled:
            return
        self.symbols, self.initial = symbols, positions.copy()
        self.pbc = pbc
        self.context = ((), np.empty((0, 3)), *context[2:])
        self.shift = np.zeros(3) if shift is None else shift.copy()
        self.center = self.initial.mean(axis=0) + self.shift
        self.delta = np.zeros(3)
        self.active = True

    def advance(self, delta):
        if not self.active:
            return
        if np.linalg.norm(delta) < 1e-12:
            self.preview.cancel()
            self.prepared = False
            self.line_actor.VisibilityOff()
            self.labels.hide()
            return
        self.delta = delta.copy()
        if not self.prepared:
            self.preview.start(self.symbols, self.initial, self.context, pbc=self.pbc)
            self.preview.move(self.initial.mean(axis=0))
            self.prepared = True
        visual = self.preview.visual
        for actor in visual.actors():
            actor.GetProperty().SetOpacity(self.opacity)
        ends = np.vstack((self.center, self.center + delta))
        set_lines(self.line_data, ends, np.array([[0, 1]]))
        self.line_actor.VisibilityOn()
        text = "  ".join(f"Δ{axis} {value:+.{self.decimals}f} Å" for axis, value in zip("xyz", delta))
        self.labels.set_text(ends.mean(axis=0)[None], [text], True)
