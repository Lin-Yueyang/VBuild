"""Display copies and camera rotations, independent of structure editing."""

from itertools import product

import numpy as np


def periodic_images(positions, cell, counts):
    if cell is None:
        return positions.copy(), np.arange(len(positions))
    shifts = np.array(list(product(*(range(count) for count in counts)))) @ cell
    sources = np.tile(np.arange(len(positions)), len(shifts))
    return (positions[None, :, :] + shifts[:, None, :]).reshape(-1, 3), sources


def rotate_vector(vector, axis, degrees):
    axis = np.asarray(axis, dtype=float)
    axis = axis / np.linalg.norm(axis)
    angle = np.deg2rad(degrees)
    return (vector * np.cos(angle) + np.cross(axis, vector) * np.sin(angle)
            + axis * np.sum(np.asarray(vector) * axis, axis=-1, keepdims=True) * (1 - np.cos(angle)))


def rotate_group(points, axis, degrees):
    center = points.mean(axis=0)
    return center + rotate_vector(points - center, axis, degrees)


def axial_orientation(view, up, axis, sign=None, *, repeat=False):
    """Carry screen-up along the shortest rotation to the chosen cell axis."""
    view = view / np.linalg.norm(view)
    target = axis / np.linalg.norm(axis)
    alignment = np.dot(view, target)
    if sign is None:
        sign = -1 if alignment < -1e-14 else 1
        if repeat and abs(alignment) > 1 - 1e-12:
            sign = -sign
    target = sign * target
    up = up - np.dot(up, view) * view
    turn_axis = np.cross(view, target)
    sine = np.linalg.norm(turn_axis)
    if sine > 1e-12 and np.dot(view, target) > -1 + 1e-12:
        angle = np.rad2deg(np.arctan2(sine, np.dot(view, target)))
        up = rotate_vector(up, turn_axis, angle)
    # For an exact half-turn, orbit about screen-up.
    up = up - np.dot(up, target) * target
    return target, up / np.linalg.norm(up)
