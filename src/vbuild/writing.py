"""Stable species grouping and equally wide numeric columns for text exports."""

from decimal import Decimal

import numpy as np


def group_order(labels, preferred=()):
    groups = dict.fromkeys((*preferred, *labels))
    for label in labels:
        groups[label] = []
    for index, label in enumerate(labels):
        groups[label].append(index)
    names = [name for name, indices in groups.items() if indices]
    order = np.array([index for name in names for index in groups[name]], dtype=int)
    return order, names, [len(groups[name]) for name in names]


def numeric_rows(array):
    text = [[str(float(value)) if value else '0.0' for value in row] for row in array]
    text = [[format(Decimal(value), 'f') if 'e' in value else value for value in row] for row in text]
    text = [[value if '.' in value else value + '.0' for value in row] for row in text]
    width = max(len(value) for row in text for value in row)
    return [' '.join(value.ljust(width, '0') for value in row) for row in text]


def cif_columns(content, count):
    lines = content.decode().splitlines()
    start = next(index for index, line in enumerate(lines) if line.strip() == '_atom_site_occupancy') + 1
    fields = [line.split() for line in lines[start:start + count]]
    widths = [max(len(row[column]) for row in fields) for column in (0, 1)]
    numbers = numeric_rows(np.array([[float(value) for value in row[2:]] for row in fields]))
    lines[start:start + count] = ['  ' + row[0].ljust(widths[0]) + '  ' + row[1].ljust(widths[1]) + '  ' + number
                                 for row, number in zip(fields, numbers)]
    return ('\n'.join(lines) + '\n').encode()


def data_columns(content, count):
    lines = content.splitlines()
    start = lines.index('Atoms # atomic') + 2
    fields = [line.split() for line in lines[start:start + count]]
    widths = [max(len(row[column]) for row in fields) for column in (0, 1)]
    numbers = numeric_rows(np.array([[float(value) for value in row[2:]] for row in fields]))
    lines[start:start + count] = ['  '.join((row[0].rjust(widths[0]), row[1].rjust(widths[1]), number))
                                 for row, number in zip(fields, numbers)]
    bounds = [index for index, line in enumerate(lines) if line.strip().endswith(('xlo xhi', 'ylo yhi', 'zlo zhi'))]
    values = [lines[index].split() for index in bounds]
    for index, fields, number in zip(bounds, values, numeric_rows(np.array([[float(x) for x in row[:2]] for row in values]))):
        lines[index] = number + '  ' + ' '.join(fields[2:])
    if 'Velocities' in lines:
        start = lines.index('Velocities') + 2
        fields = [line.split() for line in lines[start:start + count]]
        numbers = numeric_rows(np.array([[float(value) for value in row[1:]] for row in fields]))
        lines[start:start + count] = [row[0].rjust(widths[0]) + '  ' + number for row, number in zip(fields, numbers)]
    return ('\n'.join(lines) + '\n').encode()


def vasp_rows(array, width=20):
    text = [[f'{value:.16f}'.replace('-0.0000000000000000', '0.0000000000000000') for value in row] for row in array]
    width = max(width, max(len(value) for row in text for value in row) + 2)
    return [''.join(value.rjust(width) for value in row) for row in text]


def vasp_header(cell, names, counts, title):
    name_width = max(5, max(map(len, names)) + 2)
    count_width = max(6, max(len(str(count)) for count in counts) + 1)
    return [title, '   1.00000000000000', *[' ' + row for row in vasp_rows(cell, 22)],
            '   ' + ''.join(name.ljust(name_width) for name in names).rstrip(),
            ''.join(str(count).rjust(count_width) for count in counts)]


def vasp_bytes(structure, title, *, preferred=(), velocities=None, coordinate_mode='direct'):
    if coordinate_mode not in ('direct', 'cartesian'):
        raise ValueError('VASP 坐标类型需要 direct 或 cartesian。')
    order, names, counts = group_order(structure.labels, preferred)
    rows = vasp_header(structure.cell, names, counts, title)
    if structure.free is not None:
        rows.append('Selective dynamics')
    rows.append('Direct' if coordinate_mode == 'direct' else 'Cartesian')
    fraction = np.linalg.solve(structure.cell.T, structure.positions[order].T).T % 1
    fraction[fraction == 1] = 0
    coordinates = fraction if coordinate_mode == 'direct' else fraction @ structure.cell
    for index, row in zip(order, vasp_rows(coordinates)):
        if structure.free is not None:
            row += '   ' + '   '.join('T' if flag else 'F' for flag in structure.free[index])
        rows.append(row)
    if velocities is not None:
        rows.extend(['Cartesian' if velocities.mode == 'cartesian' else 'Direct',
                     *vasp_rows(velocities.values[order])])
    return ('\n'.join(rows) + '\n').encode('utf-8')
