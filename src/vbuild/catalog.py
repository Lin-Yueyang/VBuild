"""Parametric carbon structures and a small, named ASE molecule library."""

from math import gcd

import numpy as np

from .draft import DraftDocument
from .model import Structure

MOLECULES = (("水 H₂O", "H2O"), ("氢气 H₂", "H2"), ("氧气 O₂", "O2"), ("氮气 N₂", "N2"),
             ("二氧化碳 CO₂", "CO2"), ("一氧化碳 CO", "CO"), ("氨 NH₃", "NH3"),
             ("甲烷 CH₄", "CH4"), ("乙烯 C₂H₄", "C2H4"), ("苯 C₆H₆", "C6H6"))


def molecule_group(name):
    from ase.build import molecule
    atoms = molecule(name)
    positions = atoms.positions - atoms.positions.mean(axis=0)
    return tuple(atoms.get_chemical_symbols()), positions


def carbon(kind, first, second, repeat, edge, space):
    from ase.build import graphene, nanotube
    if not np.isfinite([edge, space]).all() or min(edge, space) <= 0 or repeat < 1:
        raise ValueError("边长、真空和重复数必须为正。")
    if kind == "graphene":
        if min(first, second) < 1 or 2 * first * second > 100000:
            raise ValueError("石墨烯重复数必须为正，构建原子数上限为 100000。")
        atoms = graphene(a=edge, size=(first, second, 1), vacuum=space / 2)
        title = f"Graphene {first}x{second} a={edge:g}"
    elif kind == "tube":
        if first < 1 or second < 0 or second > first:
            raise ValueError("手性指数需要 n ≥ 1 且 0 ≤ m ≤ n。")
        count = 4 * (first * first + first * second + second * second) // gcd(2 * first + second, 2 * second + first)
        if count * repeat > 100000:
            raise ValueError("碳纳米管构建原子数上限为 100000，请减少手性指数或轴向重复。")
        atoms = nanotube(first, second, length=repeat, bond=edge, vacuum=space)
        title = f"Nanotube ({first},{second}) x{repeat} bond={edge:g}"
    else:
        raise ValueError("未知结构模板。")
    symbols = tuple(atoms.get_chemical_symbols())
    structure = Structure(symbols, np.array(atoms.cell), atoms.positions.copy(), None, None,
                          symbols, (None,) * len(atoms))
    return DraftDocument(structure, title)
