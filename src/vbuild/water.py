"""Reproducible water placement in a Cartesian-z region of a periodic cell."""

from dataclasses import dataclass
from itertools import product

import numpy as np
from ase.build import molecule
from scipy.spatial import cKDTree
from scipy.spatial.transform import Rotation
from scipy.stats import qmc

from .contacts import radii_for

MASS_VOLUME = 18.0153 / (6.02214076e23 * 1e-24)
SPACING = MASS_VOLUME ** (1 / 3)
WATER = molecule("H2O").positions
WATER = WATER - WATER[0]
WATER_RADII = radii_for(("O", "H", "H"))[0]


@dataclass(frozen=True)
class Region:
    cell: np.ndarray
    lower: float
    upper: float

    @classmethod
    def from_structure(cls, structure, lower, upper):
        cell = structure.cell
        if cell is None or not all(structure.pbc):
            raise ValueError("添加水需要周期性晶胞，且三个方向均为周期边界。")
        if not np.allclose(cell[:2, 2], 0, atol=1e-8) or cell[2, 2] <= 0:
            raise ValueError("本版添加水要求 a、b 位于 xy 平面，c 的 z 分量为正。请先调整晶胞方向。")
        if not 0 <= lower < upper <= cell[2, 2]:
            raise ValueError(f"z 范围需要满足 0 ≤ 下限 < 上限 ≤ {cell[2, 2]:.2f} Å。")
        return cls(cell.copy(), float(lower), float(upper))

    @property
    def volume(self):
        return abs(np.linalg.det(self.cell)) * (self.upper - self.lower) / self.cell[2, 2]

    @property
    def recommended_grid(self):
        spans = (*np.linalg.norm(self.cell[:2], axis=1), self.upper - self.lower)
        return tuple(int(length // SPACING) for length in spans)

    def points(self, unit):
        fraction = np.asarray(unit).copy()
        fraction[:, 2] = (self.lower + fraction[:, 2] * (self.upper - self.lower)) / self.cell[2, 2]
        return fraction @ self.cell


class Neighbors:
    """Periodic image tree; owner indices retain radii and molecule identity."""

    def __init__(self, cell, positions, radii, cutoff):
        self.cell, self.radii = cell, np.asarray(radii)
        counts = np.ceil(cutoff * np.linalg.norm(np.linalg.inv(cell), axis=0)).astype(int)
        shifts = np.asarray(list(product(*(range(-count, count + 1) for count in counts)))) @ cell
        wrapped = (np.asarray(positions).reshape(-1, 3) @ np.linalg.inv(cell) % 1) @ cell
        images = (wrapped[None, :, :] + shifts[:, None, :]).reshape(-1, 3)
        self.owners = np.tile(np.arange(len(wrapped)), len(shifts))
        self.tree = cKDTree(images)

    def blocked(self, points, radius, factor=1.0):
        points = (np.asarray(points) @ np.linalg.inv(self.cell) % 1) @ self.cell
        cutoff = factor * (radius + self.radii.max())
        candidates = self.tree.query_ball_point(points, cutoff)
        result = np.zeros(len(points), dtype=bool)
        for index, nearby in enumerate(candidates):
            if nearby:
                distances = np.linalg.norm(self.tree.data[nearby] - points[index], axis=1)
                limits = factor * (radius + self.radii[self.owners[nearby]])
                result[index] = np.any(distances < limits)
        return result


def free_volume(structure, region):
    radii, missing = radii_for(structure.symbols)
    if missing:
        raise ValueError("缺少范德华半径，无法估算剩余体积：" + ", ".join(missing))
    if not len(radii):
        return region.volume, 0.0
    points = region.points(qmc.Sobol(3, scramble=True, seed=0).random_base2(14))
    neighbors = Neighbors(region.cell, structure.positions, radii, radii.max())
    available = ~neighbors.blocked(points, 0)
    ratio = available.mean()
    # A sampling-resolution indicator, not a rigorous thermodynamic uncertainty.
    resolution = region.volume * np.sqrt(ratio * (1 - ratio) / len(points))
    return region.volume * ratio, resolution


def density(count, volume):
    return MASS_VOLUME * count / volume if volume > 0 else float("inf")


def place_water(structure, region, *, count=None, grid=None, seed=0, factor=0.65,
                cancelled=lambda: False, progress=lambda _value: None):
    radii, missing = radii_for(structure.symbols)
    if missing:
        raise ValueError("缺少范德华半径，无法检查水与现有原子的避碰：" + ", ".join(missing))
    rng = np.random.default_rng(seed)
    total = int(np.prod(grid)) if grid is not None else count
    if not 1 <= total <= 5000:
        raise ValueError("每次添加水数量应在 1–5000 之间。")
    existing = None if not len(radii) else Neighbors(
        region.cell, structure.positions, radii, factor * (radii.max() + WATER_RADII.max()))
    waters = []
    placed = None
    oxygen_tree = None
    cells = list(product(*(range(number) for number in grid))) if grid is not None else None
    # Never silently accept a partial placement when a dense request cannot fit.
    for number in range(total):
        accepted = False
        for attempt in range(600):
            if cancelled():
                return None
            unit = rng.random(3)
            if cells is not None:
                unit = (np.asarray(cells[number]) + 0.5 + (unit - 0.5) * 0.35) / grid
            center = region.points(unit[None, :])[0]
            candidate = WATER @ Rotation.random(random_state=rng).as_matrix().T + center
            if np.any(candidate[:, 2] < region.lower) or np.any(candidate[:, 2] > region.upper):
                continue
            if existing is not None and any(existing.blocked(candidate[i:i+1], WATER_RADII[i], factor)[0] for i in range(3)):
                continue
            if placed is not None:
                if any(placed.blocked(candidate[i:i+1], WATER_RADII[i], factor)[0] for i in range(3)):
                    continue
                if oxygen_tree.blocked(candidate[:1], 0)[0]:
                    continue
            accepted = True
            break
        if not accepted:
            raise ValueError(f"避碰后仅能放入 {number}/{total} 个水分子，未添加任何原子。请减少数量或扩大 z 范围。")
        waters.append(candidate)
        flat = np.asarray(waters).reshape(-1, 3)
        placed = Neighbors(region.cell, flat, np.tile(WATER_RADII, len(waters)), 2 * WATER_RADII.max() * factor)
        oxygen_tree = Neighbors(region.cell, np.asarray(waters)[:, 0], np.full(len(waters), 2.4), 2.4)
        progress(number + 1)
    return ("O", "H", "H") * total, np.asarray(waters).reshape(-1, 3)
