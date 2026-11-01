"""Conventional lattice axes without idealizing positions or losing atom origins."""

import numpy as np
from ase import Atoms
from ase.data import atomic_numbers
from ase.geometry import find_mic


def conventional_atoms(structure):
    import spglib

    cell = structure.cell
    fractional = structure.positions @ np.linalg.inv(cell)
    numbers = [atomic_numbers[symbol] for symbol in structure.symbols]
    source = (cell, fractional, numbers)
    standard = spglib.standardize_cell(source, to_primitive=False, no_idealize=True, symprec=0.01)
    if standard is None:
        raise ValueError("无法识别常规晶胞，请选择当前晶胞基准。")
    new_cell, scaled, types = standard
    if len(types) < len(numbers):
        raise ValueError("常规化会缩小当前扩胞并丢失逐原子信息，请选择当前晶胞基准，或先使用单个 bulk 晶胞。")
    positions = scaled @ new_cell
    mapping = []
    # no_idealize keeps world coordinates and the original origin.
    for point, number in zip(positions, types):
        candidates = np.flatnonzero(np.asarray(numbers) == number)
        distances = find_mic(structure.positions[candidates] - point, cell, pbc=True)[1]
        selected = int(np.argmin(distances))
        if distances[selected] > 1e-6:
            raise ValueError("常规晶胞原子不能精确映射到原结构，未改变结构。请使用当前晶胞基准。")
        mapping.append(candidates[selected])
    atoms = Atoms(numbers=types, scaled_positions=scaled, cell=new_cell, pbc=True)
    atoms.new_array("source", np.array(mapping, dtype=int))
    return atoms
