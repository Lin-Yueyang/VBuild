"""Cell-aligned and Cartesian guides, with distances measured in Å."""

from itertools import product
import numpy as np


def defaults():
    return dict(show=False, basis='cell', axes=[True] * 3, grids=[False] * 3, planes=[False] * 3,
                positions=[None] * 3, interval=3., decimals=0, offset=.8,
                font=14, width=1.5, axis_opacity=.85, grid_opacity=.3, plane_opacity=.1)


def bounds(structure, periods=(1, 1, 1)):
    if structure.cell is not None:
        points = np.array(list(product((0, 1), repeat=3))) * periods @ structure.cell
    else:
        points = structure.positions
    return np.array([points.min(axis=0), points.max(axis=0)]) if len(points) else None


def coordinate_frame(structure, periods, mode):
    if mode == 'cell':
        lengths = np.linalg.norm(structure.cell, axis=1)
        return np.array([np.zeros(3), lengths * periods]), structure.cell / lengths[:, None]
    return bounds(structure, periods), np.eye(3)


def ticks(low, high, interval):
    first = int(np.ceil(low / interval - 1e-9))
    last = int(np.floor(high / interval + 1e-9))
    if last - first >= 1000:
        raise ValueError('每个方向最多显示 1000 个刻度，请增大刻度间隔。')
    return np.arange(first, last + 1) * interval


def geometry(limits, options, basis=None):
    basis = np.eye(3) if basis is None else basis
    low, high = limits
    values = [ticks(a, b, options['interval']) for a, b in zip(low, high)]
    result = []
    tick_size = max(.08, options['offset'] * .2)
    zero_axis = int(np.argmax(options['axes']))
    for axis in range(3):
        others = [number for number in range(3) if number != axis]
        origin = low - options['offset']
        start, end = origin.copy(), origin.copy()
        cell_mode = options['basis'] == 'cell'
        if cell_mode:
            end[axis] += high[axis] - low[axis]
        else:
            start[axis], end[axis] = low[axis], high[axis]
        lines, labels, positions = [[start, end]], [], []
        for value in values[axis]:
            mark = origin.copy()
            mark[axis] = origin[axis] + value - low[axis] if cell_mode else value
            tip = mark.copy()
            tip[others[0]] -= tick_size
            lines.append([mark, tip])
            label = mark.copy()
            label[others] -= tick_size * 3
            positions.append(label)
            text = f'{value if abs(value) > 1e-10 else 0:.{options["decimals"]}f}'
            labels.append('' if cell_mode and axis != zero_axis and abs(value) < 1e-10 else text)
        positions.append(end.copy())
        labels.append(('abc' if cell_mode else 'XYZ')[axis] + ' (Å)')
        level = low[axis] if options['positions'][axis] is None else options['positions'][axis]
        corner = low.copy()
        corner[axis] = level
        one, two, opposite = corner.copy(), corner.copy(), corner.copy()
        one[others[0]] = high[others[0]]
        two[others[1]] = high[others[1]]
        opposite[others] = high[others]
        grid = []
        for direction, other in (others, others[::-1]):
            for value in values[direction]:
                first, second = corner.copy(), corner.copy()
                first[direction] = second[direction] = value
                second[other] = high[other]
                grid.append([first, second])
        result.append(dict(lines=np.array(lines) @ basis, positions=np.array(positions) @ basis, labels=labels,
                           grid=np.array(grid).reshape(-1, 2, 3) @ basis,
                           plane=np.array([corner, one, opposite, two]) @ basis))
    return result
