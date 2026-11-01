"""Resolve session-local atom overrides without changing portable element styles."""

import numpy as np


def atom_values(profile, symbol, overrides, index):
    base = profile["elements"][symbol]
    return base | overrides[index] if index in overrides else base


def entries(profile, symbols, overrides, sources=None):
    indices = range(len(symbols)) if sources is None else sources
    return [atom_values(profile, symbol, overrides, int(index)) for symbol, index in zip(symbols, indices)]


def model(entry, kind):
    return kind if entry["representation"] == "inherit" else entry["representation"]


def shown(values):
    return np.array([entry["show"] and entry["opacity"] > 0 for entry in values], dtype=bool)


def visible_bonds(bonds, values, kind):
    allowed = shown(values) & np.array([entry["bonds"] and model(entry, kind) != "space" for entry in values], dtype=bool)
    return bonds[np.all(allowed[bonds], axis=1)]
