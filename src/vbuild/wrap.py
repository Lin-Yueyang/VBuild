"""Choose equivalent periodic images without breaking finite bonded molecules."""

from dataclasses import dataclass

import numpy as np

from .bonds import bond_images


@dataclass
class Wrapped:
    positions: np.ndarray
    changed: list[int]
    groups: int
    networks: int

    def apply(self, structure):
        structure.positions[:] = self.positions


def wrap_molecules(structure, factor=1.15, ranges=None):
    pbc = np.asarray(structure.pbc, dtype=bool)
    if structure.cell is None or not pbc.any():
        raise ValueError("周期归位需要晶胞及至少一个周期方向。")
    pairs, offsets = bond_images(structure.symbols, structure.positions, structure.cell,
                                 factor, ranges, pbc=pbc)
    neighbours = [[] for _symbol in structure.symbols]
    for (first, second), offset in zip(pairs, offsets):
        neighbours[first].append((int(second), offset))
        neighbours[second].append((int(first), -offset))
    images = np.zeros((len(structure.symbols), 3), dtype=int)
    visited = np.zeros(len(images), dtype=bool)
    result = structure.positions.copy()
    inverse = np.linalg.inv(structure.cell)
    groups = networks = 0
    for root in range(len(images)):
        if visited[root]:
            continue
        visited[root] = True
        pending, members, winding = [root], [], False
        while pending:
            first = pending.pop()
            members.append(first)
            for second, offset in neighbours[first]:
                image = images[first] + offset
                if visited[second]:
                    winding |= not np.array_equal(images[second], image)
                else:
                    images[second], visited[second] = image, True
                    pending.append(second)
        coordinates = structure.positions[members]
        if winding:
            # A crystal network has no finite whole-molecule image in a single cell.
            translations = np.zeros_like(coordinates)
            translations[:, pbc] = np.floor((coordinates @ inverse)[:, pbc] + 1e-10)
            result[members] = coordinates - translations @ structure.cell
            networks += 1
        else:
            coordinates = coordinates + images[members] @ structure.cell
            translation = np.zeros(3)
            translation[pbc] = np.floor((coordinates.mean(axis=0) @ inverse)[pbc] + 1e-10)
            result[members] = coordinates - translation @ structure.cell
            groups += int(np.any(np.abs(result[members] - structure.positions[members]) > 1e-8))
    changed = np.flatnonzero(np.any(np.abs(result - structure.positions) > 1e-8, axis=1)).tolist()
    unchanged = np.ones(len(images), dtype=bool)
    unchanged[changed] = False
    result[unchanged] = structure.positions[unchanged]
    return Wrapped(result, changed, groups, networks)
