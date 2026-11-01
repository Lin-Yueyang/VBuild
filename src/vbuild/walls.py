"""Cartesian z soft walls, applied only where constraints allow z motion."""

from dataclasses import dataclass

import numpy as np
from ase.calculators.calculator import Calculator, all_changes

from .thermal import project


@dataclass(frozen=True)
class Wall:
    enabled: bool = False
    position: float = 20
    spring: float = 4


@dataclass(frozen=True)
class Walls:
    upper: Wall = Wall()
    lower: Wall = Wall(position=0)

    def validate(self):
        for wall in (self.upper, self.lower):
            if wall.enabled and (not np.isfinite([wall.position, wall.spring]).all() or wall.spring <= 0):
                raise ValueError("墙位置必须有限，弹簧系数必须大于零。")
        if self.upper.enabled and self.lower.enabled and self.lower.position >= self.upper.position:
            raise ValueError("下墙位置必须低于上墙位置。")

    def contribution(self, positions, movable):
        forces = np.zeros_like(positions)
        energy = 0.0
        for wall, upper in ((self.upper, True), (self.lower, False)):
            if wall.enabled:
                delta = positions[:, 2] - wall.position
                outside = movable & ((delta > 0) if upper else (delta < 0))
                energy += 0.5 * wall.spring * float(np.sum(delta[outside] ** 2))
                forces[outside, 2] -= wall.spring * delta[outside]
        return energy, forces


class WallCalculator(Calculator):
    implemented_properties = ["energy", "forces"]

    def __init__(self, base, walls, cell, free, count):
        super().__init__()
        walls.validate()
        self.base, self.walls = base, walls
        direction = np.tile([0., 0., 1.], (count, 1))
        self.movable = np.linalg.norm(project(direction, cell, free), axis=1) > 1e-12

    def calculate(self, atoms=None, properties=("energy", "forces"), system_changes=all_changes):
        super().calculate(atoms, properties, system_changes)
        energy = self.base.get_potential_energy(atoms)
        forces = self.base.get_forces(atoms)
        wall_energy, wall_force = self.walls.contribution(atoms.positions, self.movable)
        self.results = {"energy": energy + wall_energy, "forces": forces + wall_force}
