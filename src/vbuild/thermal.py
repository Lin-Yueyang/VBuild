"""Kinetic temperature and constrained thermal velocities, in K and Å/fs."""

import numpy as np
from ase.data import atomic_masses, atomic_numbers
from ase import units

# 1 amu * (Å/fs)^2 in eV. ASE supplies the masses and physical constants.
ENERGY = units._amu * 1e10 / units._e


def masses(symbols: tuple[str, ...]) -> np.ndarray:
    return atomic_masses[[atomic_numbers[symbol] for symbol in symbols]]


def free_directions(count: int, free: np.ndarray | None) -> np.ndarray:
    return np.ones((count, 3), dtype=bool) if free is None else free


def project(velocities: np.ndarray, cell: np.ndarray, free: np.ndarray | None) -> np.ndarray:
    flags = free_directions(len(velocities), free)
    result = np.zeros_like(velocities, dtype=float)
    for allowed in np.unique(flags, axis=0):
        indices = np.all(flags == allowed, axis=1)
        if allowed.any():
            basis, _upper = np.linalg.qr(cell[allowed].T, mode="reduced")
            result[indices] = velocities[indices] @ basis @ basis.T
    return result


def temperature(symbols: tuple[str, ...], velocities: np.ndarray,
                cell: np.ndarray, free: np.ndarray | None, *, weights=None) -> float:
    degrees = int(free_directions(len(symbols), free).sum())
    if degrees == 0:
        return 0.0
    values = project(velocities, cell, free)
    weights = masses(symbols) if weights is None else weights
    twice_energy = ENERGY * np.sum(weights[:, None] * values ** 2)
    return float(twice_energy / (degrees * units.kB))


def generate(symbols: tuple[str, ...], cell: np.ndarray, free: np.ndarray | None,
             target: float, seed: int | None = None) -> np.ndarray:
    if not np.isfinite(target) or target < 0:
        raise ValueError("温度必须是有限非负数，单位为 K。")
    degrees = int(free_directions(len(symbols), free).sum())
    if target == 0:
        return np.zeros((len(symbols), 3))
    if degrees == 0:
        raise ValueError("全部原子固定，没有可动自由度；请将温度设为 0 K 或释放约束。")
    rng = np.random.default_rng(seed)
    sigma = np.sqrt(units.kB * target / (ENERGY * masses(symbols)))
    values = project(rng.standard_normal((len(symbols), 3)) * sigma[:, None], cell, free)
    current = temperature(symbols, values, cell, free)
    return values * np.sqrt(target / current)
