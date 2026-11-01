"""Coordination convex hulls with explicit periodic neighbor images."""

import numpy as np
from ase.data import atomic_numbers, covalent_radii

from .bonds import pair_key


def coordination(symbols, points, cell, factor, ranges, centers=(), neighbors=(), *, pbc=None):
    if not symbols:
        return np.empty((0, 3)), np.empty((0, 3), dtype=int), np.empty(0, dtype=int)
    from ase.neighborlist import primitive_neighbor_list
    from scipy.spatial import ConvexHull
    radii = covalent_radii[[atomic_numbers[symbol] for symbol in symbols]] * factor
    ranges = {pair_key(*pair.split("-")): bounds for pair, bounds in ranges.items()}
    cutoff = max([float(2 * radii.max()), *(value[1] for value in ranges.values())])
    periodic = (cell is not None,) * 3 if pbc is None else pbc
    box = cell if cell is not None else np.diag(np.maximum(np.ptp(points, axis=0), 1))
    first, second, vectors = primitive_neighbor_list("ijD", periodic, box, points, cutoff, self_interaction=False)
    distances = np.linalg.norm(vectors, axis=1)
    groups = {}
    for a, b, vector, distance in zip(first, second, vectors, distances):
        low, high = ranges.get(pair_key(symbols[a], symbols[b]), (0, radii[a] + radii[b]))
        if distance > 1e-8 and low <= distance < high and (not centers or symbols[a] in centers) and (not neighbors or symbols[b] in neighbors):
            groups.setdefault(int(a), []).append(points[a] + vector)
    vertices, faces, owners = [], [], []
    for index, group in groups.items():
        group = np.unique(np.asarray(group), axis=0)
        if len(group) >= 4 and np.linalg.matrix_rank(group - group.mean(axis=0)) == 3:
            hull = ConvexHull(group)
            triangles = hull.simplices.copy()
            corners = group[triangles]
            normals = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
            reverse = np.einsum("ij,ij->i", normals, hull.equations[:, :3]) < 0
            triangles[reverse] = triangles[reverse][:, [0, 2, 1]]
            faces.extend(triangles + len(vertices))
            owners.extend([index] * len(hull.simplices))
            vertices.extend(group)
    return (np.asarray(vertices, dtype=float).reshape(-1, 3), np.asarray(faces, dtype=int).reshape(-1, 3),
            np.asarray(owners, dtype=int))
