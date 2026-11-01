"""Translation-preserving primitive cells and bounded surface-cell searches."""

from dataclasses import dataclass
from math import gcd

import numpy as np
from ase import Atoms
from ase.build import make_supercell
from ase.cell import Cell
from ase.geometry import find_mic
from scipy.spatial import cKDTree
import spglib

from .build import bezout, mapped_structure, source_atoms


@dataclass
class Reduced:
    atoms: Atoms
    basis: np.ndarray
    error: float
    surface: bool = True


@dataclass
class Candidate:
    matrix: np.ndarray
    lengths: np.ndarray
    angles: np.ndarray
    count: int
    multiple: int
    mismatch: float | None


def species(structure):
    """Only merge translational copies with the same element, POTCAR label and flags."""
    keys = [(symbol, label, None if structure.free is None else tuple(structure.free[i]))
            for i, (symbol, label) in enumerate(zip(structure.symbols, structure.labels))]
    unique = {key: i + 1 for i, key in enumerate(dict.fromkeys(keys))}
    return np.array([unique[key] for key in keys])


def gauss_basis(basis, cell):
    u, v = basis.copy()
    while True:
        if np.linalg.norm(v @ cell) < np.linalg.norm(u @ cell) - 1e-10:
            u, v = v, u
        first, second = u @ cell, v @ cell
        multiple = int(np.rint(first @ second / (first @ first)))
        if multiple == 0:
            break
        v -= multiple * u
    result = np.array([u, v])
    if np.linalg.det(result) < 0:
        result[1] *= -1
    if (result[0] @ cell) @ cell[0] < 0:
        result *= -1
    return result


def translation_basis(translations, cell, tolerance):
    """Exact rank-two lattice from a finite translation subgroup (row Hermite basis)."""
    count = len(translations)
    generators = np.vstack((np.eye(2, dtype=int) * count, np.rint(translations * count).astype(int)))
    residual = translations - np.rint(translations * count) / count
    if np.max(np.linalg.norm(residual @ cell, axis=1)) > tolerance:
        raise ValueError('平移对称性不能映射为有理晶格；请减小识别容差。')
    x, y = count, 0
    for a, b in generators:
        common, p, q = bezout(x, a)
        x, y = common, p * y + q * int(b)
    height = gcd(*(int(b) - int(a) // x * y for a, b in generators))
    basis = np.array([[x, y % height], [0, height]], dtype=float) / count
    if not np.isclose(np.linalg.det(basis) * count, 1, atol=1e-9):
        raise ValueError('识别的面内平移不构成完整晶格；请减小识别容差。')
    return gauss_basis(basis, cell)


def representatives(structure, cell, types, copies, tolerance, *, surface):
    """Keep original coordinates and provenance, validating every removed copy."""
    scaled = structure.positions @ np.linalg.inv(cell)
    wrapped = scaled % 1
    tree = cKDTree(wrapped, boxsize=1)
    radius = tolerance / np.linalg.svd(cell, compute_uv=False).min()
    remaining = np.ones(len(types), dtype=bool)
    sources, error = [], 0.0
    for i in range(len(types)):
        if not remaining[i]:
            continue
        near = np.array(tree.query_ball_point(wrapped[i], radius), dtype=int)
        near = near[(types[near] == types[i]) & remaining[near]]
        distances = find_mic(structure.positions[near] - structure.positions[i], cell, pbc=True)[1]
        group = near[distances <= tolerance]
        if len(group) != copies:
            raise ValueError('缩胞后原子无法一一对应；请减小识别容差，检查重叠原子。')
        remaining[group] = False
        sources.append(i)
        error = max(error, float(distances[distances <= tolerance].max()))
    sources = np.array(sources, dtype=int)
    # A slab keeps its normal coordinates, including its bottom offset and vacuum.
    scaled[:, :2 if surface else 3] %= 1
    atoms = Atoms(symbols=[structure.symbols[i] for i in sources],
                  positions=scaled[sources] @ cell, cell=cell, pbc=True)
    atoms.new_array('source', sources)
    return atoms, error


def primitive(structure, tolerance=.001, *, surface=True):
    atoms = source_atoms(structure)
    if not len(atoms):
        raise ValueError('改胞需要至少一个原子。')
    if not np.isfinite(tolerance) or tolerance <= 0:
        raise ValueError('识别容差必须为正数。')
    cell, types = structure.cell, species(structure)
    source = (cell, structure.positions @ np.linalg.inv(cell), types)
    if surface:
        symmetry = spglib.get_symmetry(source, symprec=tolerance)
        if symmetry is None:
            raise ValueError('无法识别平移对称性，请检查晶胞和重叠原子。')
        translations = symmetry['translations'][np.all(symmetry['rotations'] == np.eye(3, dtype=int), axis=(1, 2))]
        height = abs(np.linalg.det(cell)) / np.linalg.norm(np.cross(*cell[:2]))
        translations = translations[np.abs(translations[:, 2] - np.rint(translations[:, 2])) * height <= tolerance]
        copies = len(translations)
        basis = np.eye(3)
        basis[:2, :2] = translation_basis(translations[:, :2], cell[:2], tolerance)
    else:
        reduced = spglib.standardize_cell(source, to_primitive=True, no_idealize=True, symprec=tolerance)
        if reduced is None:
            raise ValueError('无法识别三维原胞，请检查结构或调整识别容差。')
        basis = reduced[0] @ np.linalg.inv(cell)
        copies = round(len(types) / len(reduced[2]))
    new_cell = basis @ cell
    if copies == 1:
        atoms.set_cell(new_cell)
        atoms.wrap()
        if surface:
            # Recompute only a/b wrapping, so c is never translated by a whole cell.
            scaled = structure.positions @ np.linalg.inv(new_cell)
            scaled[:, :2] %= 1
            atoms.positions[:] = scaled @ new_cell
        return Reduced(atoms, basis, 0.0, surface)
    atoms, error = representatives(structure, new_cell, types, copies, tolerance, surface=surface)
    return Reduced(atoms, basis, error, surface)


def normal_c(reduced, original_cell):
    """Explicitly change the lateral periodic offset across a slab's vacuum."""
    atoms = reduced.atoms.copy()
    cell = np.asarray(atoms.cell).copy()
    normal = np.cross(cell[0], cell[1])
    normal /= np.linalg.norm(normal)
    cell[2] = normal * (cell[2] @ normal)
    atoms.set_cell(cell, scale_atoms=False)
    return Reduced(atoms, cell @ np.linalg.inv(original_cell), reduced.error)


def search(reduced, kind='rectangle', angles=(90, 90, 90), angle_tolerance=1,
           max_multiple=36, bound=8, length_tolerance=5, target=None, cancelled=lambda: False, limit=30):
    """Enumerate integer a/b vectors, with no strain and no change to c."""
    cell, count = np.asarray(reduced.atoms.cell), len(reduced.atoms)
    if kind in ('primitive', 'primitive3d'):
        return [Candidate(np.eye(3, dtype=int), Cell(cell).lengths(), Cell(cell).angles(), count, 1, None)]
    angles = np.asarray(angles, dtype=float)
    if angles.shape != (3,) or not np.isfinite(angles).all() or (angles <= 0).any() or (angles >= 180).any():
        raise ValueError('目标角度需要在 0° 与 180° 之间。')
    if bound < 1 or max_multiple < 1 or angle_tolerance <= 0 or length_tolerance <= 0:
        raise ValueError('搜索边界、面积倍数和容差需要正数。')
    target = None if target is None else np.asarray(target, dtype=float)
    if target is not None and (target.shape != (2,) or not np.isfinite(target).all() or (target <= 0).any()):
        raise ValueError('目标 a/b 边长需要两个正数。')
    values = np.arange(-bound, bound + 1)
    vectors = np.array(np.meshgrid(values, values)).reshape(2, -1).T
    vectors = vectors[np.any(vectors, axis=1)]
    real = vectors @ cell[:2]
    lengths = np.linalg.norm(real, axis=1)
    c_length = np.linalg.norm(cell[2])
    to_c = np.degrees(np.arccos(np.clip(real @ cell[2] / lengths / c_length, -1, 1)))
    ranked = []
    for i, first in enumerate(vectors):
        if cancelled():
            return []
        if first[0] < 0 or first[0] == 0 and first[1] < 0:
            continue
        determinants = first[0] * vectors[:, 1] - first[1] * vectors[:, 0]
        gamma = np.degrees(np.arccos(np.clip(real @ real[i] / lengths / lengths[i], -1, 1)))
        errors = np.maximum.reduce((np.abs(to_c - angles[0]), np.full(len(vectors), abs(to_c[i] - angles[1])), np.abs(gamma - angles[2])))
        mask = (determinants > 0) & (determinants <= max_multiple) & (errors <= angle_tolerance)
        aspect = np.abs(lengths - lengths[i]) / np.minimum(lengths, lengths[i]) * 100
        if kind == 'square':
            mask &= aspect <= length_tolerance
        mismatch = None
        if target is not None:
            mismatch = np.maximum(abs(lengths[i] / target[0] - 1), np.abs(lengths / target[1] - 1)) * 100
            mask &= mismatch <= length_tolerance
        for j in np.flatnonzero(mask):
            multiple = int(determinants[j])
            matrix = np.eye(3, dtype=int)
            matrix[:2, :2] = [first, vectors[j]]
            candidate = Candidate(matrix, np.array([lengths[i], lengths[j], c_length]),
                                  np.array([to_c[j], to_c[i], gamma[j]]), count * multiple, multiple,
                                  None if mismatch is None else float(mismatch[j]))
            rank = (multiple, errors[j], aspect[j] if kind == 'square' else lengths[i] + lengths[j],
                    np.linalg.norm(matrix - np.eye(3)))
            if target is not None:
                rank = (candidate.mismatch,) + rank
            ranked.append((rank, candidate))
    ranked.sort(key=lambda item: item[0])
    candidates, seen = [], set()
    for _rank, candidate in ranked:
        key = tuple(np.round(np.r_[candidate.lengths, candidate.angles], 5))
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)
        if limit is not None and len(candidates) == limit:
            break
    return candidates


def unit_candidate(reduced, candidate):
    """Shortest independent a/b repeats along the chosen candidate's directions."""
    matrix = candidate.matrix.copy()
    repeats = np.array([gcd(*map(int, matrix[i])) for i in range(2)] + [1])
    matrix[:2] //= repeats[:2, None]
    cell = Cell(matrix @ reduced.atoms.cell)
    multiple = abs(round(np.linalg.det(matrix)))
    unit = Candidate(matrix, cell.lengths(), cell.angles(), len(reduced.atoms)*multiple, multiple, None)
    return unit, tuple(map(int, repeats))


def build(structure, reduced, candidate, *, align=True):
    atoms = make_supercell(reduced.atoms, candidate.matrix, order='atom-major', wrap=False)
    scaled = atoms.get_scaled_positions(wrap=False)
    scaled[:, :2 if reduced.surface else 3] %= 1
    atoms.positions[:] = scaled @ atoms.cell
    rotation = np.eye(3)
    if align:
        direction = atoms.cell[0] / np.linalg.norm(atoms.cell[0])
        normal = np.cross(atoms.cell[0], atoms.cell[1])
        normal /= np.linalg.norm(normal)
        rotation = np.column_stack((direction, np.cross(normal, direction), normal))
        cell = np.asarray(atoms.cell) @ rotation
        cell[abs(cell) < 1e-12] = 0
        atoms.positions[:] = atoms.positions @ rotation
        atoms.set_cell(cell)
    return mapped_structure(structure, atoms, rotation)
