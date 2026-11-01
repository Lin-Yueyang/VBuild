"""Advisory short-distance checks using Alvarez van der Waals radii."""

import numpy as np
from ase.data import atomic_numbers
from ase.data.vdw_alvarez import vdw_radii


def radii_for(symbols):
    table = np.full(119, np.nan)
    table[:len(vdw_radii)] = vdw_radii
    values = table[[atomic_numbers[symbol] for symbol in symbols]]
    missing = tuple(dict.fromkeys(symbol for symbol, value in zip(symbols, values) if not np.isfinite(value)))
    return values, missing


def short_pairs(symbols, positions, cell, factor=0.30, *, pbc=None):
    radii, missing = radii_for(symbols)
    valid = np.flatnonzero(np.isfinite(radii))
    if len(valid) < 2:
        return np.empty((0, 4)), missing
    from ase.neighborlist import primitive_neighbor_list
    periodic = cell is not None
    box = cell if periodic else np.diag(np.maximum(np.ptp(positions, axis=0), 1))
    first, second, distances = primitive_neighbor_list(
        "ijd", [periodic] * 3 if pbc is None else pbc, box, positions[valid],
        cutoff=2 * radii[valid].max() * factor, self_interaction=False)
    limits = factor * (radii[valid[first]] + radii[valid[second]])
    keep = (first < second) & (distances < limits)
    pairs = {}
    for a, b, distance, limit in zip(valid[first[keep]], valid[second[keep]], distances[keep], limits[keep]):
        key = (int(a), int(b))
        if key not in pairs or distance < pairs[key][0]:
            pairs[key] = (float(distance), float(limit))
    rows = [(a, b, *values) for (a, b), values in sorted(pairs.items())]
    return np.asarray(rows, dtype=float).reshape(-1, 4), missing
