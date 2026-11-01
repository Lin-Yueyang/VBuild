"""Integer supercells and Miller slabs with explicit atom/constraint mapping."""

from dataclasses import dataclass
from itertools import product
from math import gcd

import numpy as np
from ase import Atoms
from ase.build import make_supercell

from .model import Structure
from .standard import conventional_atoms


def projector(vectors):
    if not len(vectors):
        return np.zeros((3, 3))
    basis, _upper = np.linalg.qr(vectors.T, mode="reduced")
    return basis @ basis.T


def map_flags(flags, old_cell, new_cell, rotation):
    if flags is None:
        return None, 0
    result = np.zeros_like(flags)
    unresolved = 0
    candidates = [(np.array(row, dtype=bool), projector(new_cell[np.array(row, dtype=bool)]))
                  for row in product((False, True), repeat=3)]
    for allowed in np.unique(flags, axis=0):
        rows = np.all(flags == allowed, axis=1)
        target = projector(old_cell[allowed] @ rotation)
        matches = [row for row, space in candidates if row.sum() == allowed.sum()
                   and np.allclose(space, target, atol=1e-9, rtol=0)]
        if matches:
            result[rows] = matches[0]
        else:
            # The dialog requires explicit consent to fully fix these atoms.
            unresolved += int(rows.sum())
    return result, unresolved


@dataclass
class Build:
    structure: Structure
    sources: np.ndarray
    unresolved: int

    def apply(self, target, *, fix_unmapped=False):
        if self.unresolved and not fix_unmapped:
            raise ValueError("部分约束无法等价表示，需要明确选择全固定后才能应用。")
        value = self.structure
        target.cell, target.positions = value.cell.copy(), value.positions.copy()
        target.free = None if value.free is None else value.free.copy()
        target.symbols, target.labels, target.origins = value.symbols, value.labels, value.origins


def mapped_structure(original, atoms, rotation):
    # Atom-major source order keeps each POSCAR species group contiguous.
    order = np.argsort(atoms.arrays["source"], kind="stable")
    sources = atoms.arrays["source"][order]
    old_flags = None if original.free is None else original.free[sources]
    flags, unresolved = map_flags(old_flags, original.cell, np.asarray(atoms.cell), rotation)
    structure = Structure(tuple(original.symbols[i] for i in sources), np.asarray(atoms.cell).copy(),
                          atoms.positions[order].copy(), flags, original.temperature,
                          tuple(original.labels[i] for i in sources),
                          tuple(original.origins[i] for i in sources), original.temperature_pending)
    return Build(structure, sources.copy(), unresolved)


def repeat_atoms(structure, matrix):
    atoms = source_atoms(structure)
    return make_supercell(atoms, matrix, order="atom-major", wrap=True)


def source_atoms(structure):
    if not all(structure.pbc):
        raise ValueError("切面/扩胞当前要求三个方向均为周期边界。")
    atoms = Atoms(symbols=structure.symbols, positions=structure.positions, cell=structure.cell, pbc=True)
    atoms.new_array("source", np.arange(len(atoms)))
    return atoms


def supercell(structure, matrix, vacuum=0):
    matrix = np.asarray(matrix)
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all() or not np.equal(matrix, np.rint(matrix)).all():
        raise ValueError("扩胞矩阵需要 3×3 个整数。")
    matrix = matrix.astype(int)
    if round(np.linalg.det(matrix)) == 0:
        raise ValueError("扩胞矩阵的行列式不能为零。")
    if not np.isfinite(vacuum) or vacuum < 0:
        raise ValueError("z 向增加真空需要非负数。")
    atoms = source_atoms(structure) if np.array_equal(matrix, np.eye(3, dtype=int)) else repeat_atoms(structure, matrix)
    if vacuum > 0:
        if not np.allclose(atoms.cell[:2, 2], 0, atol=1e-9, rtol=0) or atoms.cell[2, 2] <= 0:
            raise ValueError("z 向增加真空要求 a/b 位于 xy 平面、c 的 z 分量为正；请先切面或调整晶胞方向。")
        cell = np.asarray(atoms.cell).copy()
        cell[2, 2] += vacuum
        atoms.set_cell(cell)
    return mapped_structure(structure, atoms, np.eye(3))


def bezout(first, second):
    old_r, r = int(first), int(second)
    old_s, s, old_t, t = 1, 0, 0, 1
    while r:
        quotient = old_r // r
        old_r, r = r, old_r - quotient * r
        old_s, s = s, old_s - quotient * s
        old_t, t = t, old_t - quotient * t
    sign = 1 if old_r >= 0 else -1
    return sign * old_r, sign * old_s, sign * old_t


def plane_basis(cell, indices):
    indices = np.asarray(indices, dtype=int)
    h, k, l = indices // gcd(*map(int, indices))
    if h or k:
        g, p, q = bezout(h, k)
        _one, r, s = bezout(g, l)
        u = np.array([k // g, -h // g, 0])
        v = np.array([p * l, q * l, -g])
        w = np.array([p * r, q * r, s])
    else:
        u, v, w = np.array([1, 0, 0]), np.array([0, l, 0]), np.array([0, 0, l])
    # Gauss reduction shortens the two in-plane lattice vectors without enlarging the cell.
    while True:
        if np.linalg.norm(v @ cell) < np.linalg.norm(u @ cell):
            u, v = v, u
        first, second = u @ cell, v @ cell
        multiple = int(np.rint(np.dot(first, second) / np.dot(first, first)))
        if multiple == 0:
            break
        v = v - multiple * u
    normal = np.linalg.solve(cell, np.array([h, k, l]))
    normal /= np.linalg.norm(normal)
    if np.dot(np.cross(u @ cell, v @ cell), normal) < 0:
        v = -v
    return np.array([u, v, w]), normal


def surface_basis(cell, indices):
    h, k, l = np.asarray(indices, dtype=int) // gcd(*map(int, indices))
    if np.count_nonzero((h, k, l)) == 1:
        return plane_basis(cell, indices)
    _g, p, q = bezout(k, l)
    first, second, third = cell
    k1 = np.dot(p * (k * first - h * second) + q * (l * first - h * third), l * second - k * third)
    k2 = np.dot(l * (k * first - h * second) - k * (l * first - h * third), l * second - k * third)
    if abs(k2) > 1e-10:
        shift = -int(round(k1 / k2))
        p, q = p + shift * l, q - shift * k
    _g, a, b = bezout(p * k + q * l, h)
    basis = np.array([(p * k + q * l, -p * h, -q * h),
                      np.array((0, l, -k)) // abs(gcd(l, k)), (b, a * p, a * q)])
    normal = np.linalg.solve(cell, [h, k, l])
    normal /= np.linalg.norm(normal)
    if np.dot(np.cross(basis[0] @ cell, basis[1] @ cell), normal) < 0:
        basis[1] *= -1
    if np.dot(basis[2] @ cell, normal) < 0:
        basis[2] *= -1
    return basis, normal


def slab(structure, indices, repeats, vacuum, bottom=0.5, *, conventional=False):
    indices = np.asarray(indices)
    if indices.shape != (3,) or not np.isfinite(indices).all() or not np.equal(indices, np.rint(indices)).all() or not indices.any():
        raise ValueError("Miller 指数需要三个整数，不能全部为零。")
    repeats = np.asarray(repeats)
    if repeats.shape != (3,) or not np.isfinite(repeats).all() or not np.equal(repeats, np.rint(repeats)).all() or (repeats < 1).any() or not np.isfinite(vacuum) or vacuum < 0:
        raise ValueError("x/y/z 重复数需要三个正整数，真空总厚度需要非负数。")
    if not np.isfinite(bottom) or bottom < 0 or vacuum > 0 and bottom > vacuum:
        raise ValueError("底部间距需要非负数，且不能超过真空总厚度。")
    bulk = conventional_atoms(structure) if conventional else source_atoms(structure)
    bulk_cell = np.asarray(bulk.cell)
    basis, normal = surface_basis(bulk_cell, indices)
    direction = basis[0] @ bulk_cell
    direction /= np.linalg.norm(direction)
    rotation = np.column_stack((direction, np.cross(normal, direction), normal))
    matrix = np.diag(repeats.astype(int)) @ basis
    atoms = make_supercell(bulk, matrix, order="atom-major", wrap=True)
    positions = atoms.positions @ rotation
    cell = np.asarray(atoms.cell) @ rotation
    cell[0, 1:] = 0
    cell[1, 2] = 0
    height = np.dot(matrix[2] @ bulk_cell, normal)
    cell[2] = [0, 0, height]
    scaled = positions @ np.linalg.inv(cell)
    scaled[:, :2] -= np.floor(scaled[:, :2] + 1e-10)
    positions = scaled @ cell
    if vacuum > 0:
        positions[:, 2] -= positions[:, 2].min()
        cell[2, 2] = np.ptp(positions[:, 2]) + vacuum
        positions[:, 2] += bottom
    atoms.set_cell(cell)
    atoms.positions[:] = positions
    return mapped_structure(structure, atoms, rotation)
