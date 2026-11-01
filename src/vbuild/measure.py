"""Geometry of clicked atoms, in their displayed Cartesian coordinates."""

import numpy as np

SIZES = {"distance": 2, "angle": 3, "dihedral": 4, "plane": 6}


def angle(first: np.ndarray, second: np.ndarray) -> float | None:
    length = np.linalg.norm(first) * np.linalg.norm(second)
    if length < 1e-12:
        return None
    return float(np.degrees(np.arccos(np.clip(np.dot(first, second) / length, -1, 1))))


def measure(mode: str, points: np.ndarray, decimals: int = 2) -> str:
    if mode == "distance":
        return f"{np.linalg.norm(points[1] - points[0]):.{decimals}f} Å"
    if mode == "angle":
        value = angle(points[0] - points[1], points[2] - points[1])
    elif mode == "dihedral":
        first, axis, last = np.diff(points, axis=0)
        length = np.linalg.norm(axis)
        if length < 1e-12:
            return "二面角无法定义（重合原子）"
        axis = axis / length
        first = -first + np.dot(first, axis) * axis
        last = last - np.dot(last, axis) * axis
        if np.linalg.norm(first) * np.linalg.norm(last) < 1e-12:
            return "二面角无法定义（共线原子）"
        value = float(np.degrees(np.arctan2(np.dot(np.cross(axis, first), last), np.dot(first, last))))
    else:
        first = np.cross(points[1] - points[0], points[2] - points[0])
        second = np.cross(points[4] - points[3], points[5] - points[3])
        value = angle(first, second)
        if value is not None:
            value = min(value, 180 - value)
    if value is None:
        return "夹角无法定义（重合或共线原子）"
    return f"{value:.{decimals}f}°"


def label_position(mode: str, points: np.ndarray) -> np.ndarray:
    """Place the value at its bond, angle arc or central dihedral bond."""
    if mode == "dihedral":
        return points[1:3].mean(axis=0)
    if mode == "angle":
        arms = points[[0, 2]] - points[1]
        lengths = np.linalg.norm(arms, axis=1)
        if np.all(lengths > 1e-12):
            direction = (arms / lengths[:, None]).sum(axis=0)
            if np.linalg.norm(direction) < 1e-12:
                direction = np.cross(arms[0], np.eye(3)[np.argmin(np.abs(arms[0]))])
            return points[1] + .5 * lengths.min() * direction / np.linalg.norm(direction)
        return points[1].copy()
    return points.mean(axis=0)


def diagram(mode: str, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """World-space dash segments, including an arc for a defined bond angle."""
    paths = []
    if mode == "plane":
        for start in (0, 3):
            group = points[start:start + 3]
            paths.extend(zip(group[:-1], group[1:]))
            if len(group) == 3:
                paths.append((group[-1], group[0]))
    else:
        paths.extend(zip(points[:-1], points[1:]))
    if mode == "angle" and len(points) == 3:
        first, second = points[0] - points[1], points[2] - points[1]
        length1, length2 = np.linalg.norm(first), np.linalg.norm(second)
        if length1 > 1e-12 and length2 > 1e-12:
            first, second = first / length1, second / length2
            cosine = np.clip(np.dot(first, second), -1, 1)
            tangent = second - cosine * first
            length = np.linalg.norm(tangent)
            if length > 1e-12:
                angles = np.linspace(0, np.arccos(cosine), 25)
                radius = 0.35 * min(length1, length2)
                arc = points[1] + radius * (np.cos(angles)[:, None] * first
                                          + np.sin(angles)[:, None] * tangent / length)
                paths.extend(zip(arc[:-1:2], arc[1::2]))
    segments = []
    for first, second in paths:
        vector = second - first
        length = np.linalg.norm(vector)
        count = max(1, int(np.ceil(length / 0.20)))
        for index in range(count):
            segments.append([first + vector * index / count,
                             first + vector * (index + 0.55) / count])
    vertices = np.asarray(segments, dtype=float).reshape(-1, 3)
    edges = np.arange(len(vertices)).reshape(-1, 2)
    return vertices, edges
