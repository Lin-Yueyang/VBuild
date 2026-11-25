"""Directional N/O/F hydrogen contacts for the display layer (distances in Å)."""

import numpy as np


def dash_vertices(symbols, positions, cell, pbc, visible):
    """Anchor each half to its displayed atom, including across periodic faces."""
    empty = np.empty((0, 3))
    elements = np.asarray(symbols, dtype=str)
    hydrogen = np.flatnonzero(elements == "H")
    heavy = np.flatnonzero(np.isin(elements, ("N", "O", "F")))
    if not hydrogen.size or not heavy.size:
        return empty
    box = None
    if all(pbc):
        lengths = np.diag(cell)
        if np.all(lengths > 6.) and np.array_equal(cell, np.diag(lengths)):
            box = lengths
    if any(pbc) and box is None:
        from ase.neighborlist import primitive_neighbor_list
        subset = np.concatenate((hydrogen, heavy))
        first, second, vectors = primitive_neighbor_list("ijD", pbc, cell, positions[subset], 3.0)
        keep = (first < hydrogen.size) & (second >= hydrogen.size)
        h, a, vectors = subset[first[keep]], subset[second[keep]], vectors[keep]
    else:
        from scipy.spatial import cKDTree
        coords = positions if box is None else np.mod(positions, box)
        if box is not None:
            coords[coords >= box] = 0.  # A tiny negative coordinate can round to the box length.
        pairs = cKDTree(coords[hydrogen], boxsize=box).sparse_distance_matrix(
            cKDTree(coords[heavy], boxsize=box), 3.0, output_type="coo_matrix")
        h, a = hydrogen[pairs.row], heavy[pairs.col]
        vectors = positions[a] - positions[h]
        if box is not None:
            vectors = vectors - np.rint(vectors / box) * box

    distance = np.linalg.norm(vectors, axis=1)
    bonded = np.flatnonzero((distance > 0) & (distance <= 1.2))
    order = bonded[np.argsort(distance[bonded], kind="stable")]
    donors, nearest = np.unique(h[order], return_index=True)
    donor_row = np.full(len(symbols), -1, dtype=int)
    donor_row[donors] = order[nearest]
    accept = np.flatnonzero((distance > 1.2) & (donor_row[h] >= 0))
    donor = donor_row[h[accept]]
    hd, ha = vectors[donor], vectors[accept]
    keep = (visible[h[accept]] & visible[a[accept]] & visible[a[donor]]
            & (np.linalg.norm(ha - hd, axis=1) <= 3.0)
            & (np.einsum("ij,ij->i", hd, ha) <= np.cos(np.deg2rad(150)) * distance[donor] * distance[accept]))
    accept = accept[keep]
    # Four dashes on either side of the midpoint. Periodic images meet exactly
    # when both cells are visible, without a line through the entire box.
    t = ((np.arange(4)[:, None] + np.array([.12, .72])) / 8).reshape(-1, 1)
    v = vectors[accept, None, :]
    left = positions[h[accept], None, :] + t * v
    right = positions[a[accept], None, :] + (t - .5) * v
    return np.concatenate((left, right), axis=1).reshape(-1, 3)
