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


def axis_up(cell, axis):
    direction = cell[axis] / np.linalg.norm(cell[axis])
    other = cell[(axis + 1) % 3]
    up = other - np.dot(other, direction) * direction
    return up / np.linalg.norm(up)
