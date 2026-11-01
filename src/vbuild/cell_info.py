"""Compact cell dimensions shared by the viewport and cell search."""

from ase.cell import Cell
from collections import Counter


def composition_text(symbols):
    return ' '.join(f'{symbol} {count}' for symbol, count in Counter(symbols).items())


def cell_text(cell):
    lengths, angles = Cell(cell).lengths(), Cell(cell).angles()
    rows = [f'<tr><td>{axis}</td><td align="right">{length:.3f} Å</td>'
            f'<td style="padding-left:12px">{name}</td><td align="right">{angle:.3f}°</td></tr>'
            for axis, length, name, angle in zip('abc', lengths, 'αβγ', angles)]
    return '<table cellspacing="2">' + ''.join(rows) + '</table>'
