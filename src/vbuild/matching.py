"""Joint surface supercell matching with explicit atom-count/misfit tradeoffs."""

from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

from .recell import Candidate, Reduced, search


@dataclass
class Match:
    current: Candidate
    reference: Candidate
    deltas: np.ndarray
    angle_delta: float

    @property
    def count(self):
        return self.current.count + self.reference.count

    @property
    def mismatch(self):
        return float(np.abs(self.deltas).max())


@dataclass
class Matches:
    reference: Reduced
    candidates: list[Match]


def order_matches(matches, priority='balanced'):
    if not matches:
        return []
    if priority == 'atoms':
        return sorted(matches, key=lambda item: (item.count, item.mismatch, item.angle_delta))
    if priority == 'misfit':
        return sorted(matches, key=lambda item: (item.mismatch, item.count, item.angle_delta))
    values = np.array([(item.count, item.mismatch) for item in matches], dtype=float)
    scaled = (values - values.min(axis=0)) / np.maximum(np.ptp(values, axis=0), 1e-12)
    scores = np.sum(scaled**2, axis=1)
    return [item for _score, item in sorted(zip(scores, matches), key=lambda pair: (pair[0], pair[1].count, pair[1].angle_delta))]


def pareto(matches):
    """Keep candidates not dominated in both total atoms and maximum edge misfit."""
    result, best = [], float('inf')
    for item in sorted(matches, key=lambda x: (x.count, round(x.mismatch, 7), x.angle_delta)):
        # Differences below rounding noise are not meaningful alternative interfaces.
        error = round(item.mismatch, 7)
        if error < best:
            result.append(item)
            best = error
    return result


def joint_search(current, reference, *, max_atoms=1000, priority='balanced',
                 kind='rectangle', angles=(90, 90, 90), angle_tolerance=1,
                 max_multiple=36, bound=8, length_tolerance=5, cancelled=lambda: False):
    """Search both lattices, never truncating either side's candidates before matching."""
    first_count, second_count = len(current.atoms), len(reference.atoms)
    if kind in ('primitive', 'primitive3d') or not 0 < length_tolerance < 100:
        raise ValueError('共同扩胞需要目标形状，边长容差必须在 0% 与 100% 之间。')
    if max_atoms < first_count + second_count:
        raise ValueError(f'合计原子上限至少需要 {first_count + second_count}。')
    options = dict(kind=kind, angles=angles, angle_tolerance=angle_tolerance, bound=bound,
                   length_tolerance=length_tolerance, cancelled=cancelled, limit=None)
    first = search(current, max_multiple=min(max_multiple, (max_atoms-second_count)//first_count), **options)
    second = search(reference, max_multiple=min(max_multiple, (max_atoms-first_count)//second_count), **options)
    if not first or not second or cancelled():
        return Matches(reference, [])
    lengths = np.array([item.lengths[:2] for item in second])
    tree = cKDTree(np.log(lengths))
    # The exact, signed definition is current/reference - 1. Logs only prefilter pairs.
    radius = max(abs(np.log(1-length_tolerance/100)), np.log(1+length_tolerance/100))
    candidates = []
    for item in first:
        if cancelled():
            return Matches(reference, [])
        near = tree.query_ball_point(np.log(item.lengths[:2]), radius + 1e-12, p=np.inf)
        for index in near:
            other = second[index]
            if item.count + other.count > max_atoms:
                continue
            deltas = (item.lengths[:2] / other.lengths[:2] - 1) * 100
            angle_delta = abs(item.angles[2] - other.angles[2])
            if np.abs(deltas).max() <= length_tolerance + 1e-9 and angle_delta <= angle_tolerance + 1e-9:
                candidates.append(Match(item, other, deltas, angle_delta))
    return Matches(reference, order_matches(pareto(candidates), priority))


def instruction(matrix):
    """Use simple repeats when possible; otherwise show the actual row transform."""
    matrix = np.asarray(matrix)
    if np.allclose(matrix, np.diag(np.diag(matrix)), atol=1e-8) and np.allclose(matrix, np.rint(matrix), atol=1e-8) and (np.diag(matrix) > 0).all():
        return '重复 ' + ' × '.join(str(int(round(value))) for value in np.diag(matrix))
    text = '[' + '; '.join(' '.join(f'{value:.5g}' for value in row) for row in matrix) + ']'
    nonzero = np.abs(matrix) > 1e-8
    if np.all(nonzero.sum(axis=0) == 1) and np.all(nonzero.sum(axis=1) == 1) and np.allclose(matrix, np.rint(matrix), atol=1e-8):
        repeats = ' × '.join(str(int(round(value))) for value in np.abs(matrix).sum(axis=0))
        return '沿原 a/b/c 重复 ' + repeats + '；换轴/反向 ' + text
    return text
