"""Display bonds from covalent radii or explicit element-pair ranges."""

import numpy as np
from ase.data import atomic_numbers, covalent_radii


def pair_key(first, second):
    return "-".join(sorted((first, second)))


def default_maximum(first, second, factor):
    return float((covalent_radii[atomic_numbers[first]] + covalent_radii[atomic_numbers[second]]) * factor)


def bond_images(symbols, positions, cell, factor=1.15, ranges=None, *, pbc=(False, False, False)):
    """Undirected bonds and lattice offsets, including bonds to periodic self images."""
    if not symbols:
        return np.empty((0, 2), dtype=int), np.empty((0, 3), dtype=int)
    radii = covalent_radii[[atomic_numbers[symbol] for symbol in symbols]] * factor
    ranges = {} if ranges is None else ranges
    cutoff = max([float(2 * radii.max()), *(bounds[1] for bounds in ranges.values())])
    if not any(pbc):
        from scipy.spatial import cKDTree
        pairs = cKDTree(positions).query_pairs(cutoff, output_type="ndarray")
        pairs = pairs[np.lexsort((pairs[:, 1], pairs[:, 0]))]
        first, second = pairs.T
        distance = np.linalg.norm(positions[second] - positions[first], axis=1)
        offsets = np.zeros((len(pairs), 3), dtype=int)
    else:
        from ase.neighborlist import primitive_neighbor_list
        first, second, distance, offsets = primitive_neighbor_list("ijdS", pbc, cell, positions,
                                                                  cutoff, self_interaction=False)
    keep = (first <= second) & (distance > 1e-8)
    first, second, distance, offsets = first[keep], second[keep], distance[keep], offsets[keep]
    minimum, maximum = np.zeros(len(first)), radii[first] + radii[second]
    elements = np.asarray(symbols)
    for pair, bounds in ranges.items():
        left, right = pair.split("-")
        matches = (((elements[first] == left) & (elements[second] == right))
                   | ((elements[first] == right) & (elements[second] == left)))
        minimum[matches], maximum[matches] = bounds
    keep = (distance >= minimum) & (distance < maximum)
    return np.column_stack((first[keep], second[keep])), offsets[keep]


def find_bonds(symbols: tuple[str, ...], positions: np.ndarray, cell: np.ndarray,
               factor: float = 1.15, ranges: dict | None = None, *, pbc=(False, False, False)) -> np.ndarray:
    pairs, _offsets = bond_images(symbols, positions, cell, factor, ranges, pbc=pbc)
    return pairs[pairs[:, 0] < pairs[:, 1]]


def connected(structure, root, factor=1.15, ranges=None):
    pairs = find_bonds(structure.symbols, structure.positions, structure.cell, factor, ranges, pbc=structure.pbc)
    neighbours = [set() for _symbol in structure.symbols]
    for first, second in pairs:
        neighbours[first].add(int(second))
        neighbours[second].add(int(first))
    selected, pending = {root}, [root]
    while pending:
        unseen = neighbours[pending.pop()] - selected
        selected.update(unseen)
        pending.extend(unseen)
    return sorted(selected)


def bond_halves(positions: np.ndarray, bonds: np.ndarray, colors: np.ndarray):
    ends = positions[bonds]
    midpoints = ends.mean(axis=1)
    vertices = np.stack((ends[:, 0], midpoints, ends[:, 1]), axis=1).reshape(-1, 3)
    start = np.arange(len(bonds)) * 3
    edges = np.stack((np.column_stack((start, start + 1)),
                      np.column_stack((start + 1, start + 2))), axis=1).reshape(-1, 2)
    tones = colors[bonds].reshape(-1, 3).copy()
    tones[np.all(tones >= 235, axis=1)] = 210
    return vertices, edges, tones
