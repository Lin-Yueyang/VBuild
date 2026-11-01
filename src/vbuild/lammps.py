"""LAMMPS metadata and mass suggestions, confirmed before reading coordinates."""

from dataclasses import dataclass, field
from io import StringIO
import re

import numpy as np
from ase.data import atomic_numbers, atomic_masses, chemical_symbols
from ase.calculators.lammps.unitconvert import convert

from .formats import from_atoms
from .source import open_source


@dataclass
class LammpsInfo:
    types: list[int]
    mapping: dict = field(default_factory=dict)
    masses: dict = field(default_factory=dict)
    units: str = "metal"
    style: str = "auto"
    pbc: tuple = (True, True, True)
    declared: set = field(default_factory=set)
    dump: bool = False

    def suggestions(self, units):
        result = {}
        for key, mass in self.masses.items():
            mass = float(convert(mass, "mass", units, "ASE"))
            candidates = np.argsort(abs(atomic_masses[1:] - mass))[:2] + 1
            result[key] = [(chemical_symbols[index], float(atomic_masses[index]), mass) for index in candidates]
        return result


def read_metadata(text, info):
    for key in ("units", "atom_style", "boundary"):
        match = re.search(r"\b" + key + r"\s*(?:[:=]\s*)?([A-Za-z]+(?:\s+[pfsm]{1,2}){0,2})", text)
        if match is None:
            continue
        value = match[1].split()
        if key == "boundary":
            if len(value) != 3 or any(re.fullmatch(r"p|pp|[fsm]{1,2}", x) is None for x in value):
                raise ValueError("LAMMPS boundary 注释需要三个 p/f/s/m 边界标记。")
            info.pbc = tuple(x in ("p", "pp") for x in value)
        else:
            setattr(info, "style" if key == "atom_style" else key, value[0])
        info.declared.add(key)


def dump_block(stream):
    first = stream.readline().strip()
    declared = None
    if first == b"ITEM: UNITS":
        declared = stream.readline().decode().strip()
        first = stream.readline().strip()
    if first != b"ITEM: TIMESTEP":
        raise ValueError("LAMMPS dump 需要 ITEM: TIMESTEP 标记。")
    step = int(stream.readline())
    if stream.readline().strip() != b"ITEM: NUMBER OF ATOMS":
        raise ValueError("LAMMPS dump 缺少原子数。")
    count = int(stream.readline())
    header = stream.readline().decode().split()
    if count <= 0 or header[:3] != ["ITEM:", "BOX", "BOUNDS"]:
        raise ValueError("LAMMPS dump 缺少有效晶胞。")
    bounds = np.array([[float(x) for x in stream.readline().split()] for _index in range(3)])
    columns = stream.readline().decode().split()
    if columns[:2] != ["ITEM:", "ATOMS"]:
        raise ValueError("LAMMPS dump 缺少 ATOMS 表头。")
    rows = [stream.readline().decode().split() for _index in range(count)]
    if any(len(row) != len(columns) - 2 for row in rows):
        raise ValueError("LAMMPS dump 坐标数量/列数不足。")
    return step, header[3:], bounds, columns[2:], np.array(rows), declared


def probe(path, dump=False):
    with open_source(path) as stream:
        if dump:
            _step, header, _box, columns, rows, declared = dump_block(stream)
            types = sorted(set(map(int, rows[:, columns.index("type")]))) if "type" in columns else []
            info = LammpsInfo(types, units=declared or "metal", dump=True,
                              pbc=tuple(x == "pp" for x in header[-3:]), declared={"boundary"})
            if declared is not None:
                info.declared.add("units")
            if "type" in columns:
                for row in rows:
                    key = int(row[columns.index("type")])
                    if "element" in columns:
                        symbol = row[columns.index("element")]
                        if symbol not in atomic_numbers or (key in info.mapping and info.mapping[key] != symbol):
                            raise ValueError("LAMMPS 类型的元素名称无效或不一致。")
                        info.mapping[key] = symbol
                    if "mass" in columns:
                        mass = float(row[columns.index("mass")])
                        if not np.isfinite(mass) or mass <= 0:
                            raise ValueError("LAMMPS 原子质量需要是有限正数。")
                        if key in info.masses and not np.isclose(info.masses[key], mass, rtol=1e-8, atol=0):
                            raise ValueError("同一个 LAMMPS 类型有不同质量，请先按元素拆分类型。")
                        info.masses[key] = mass
            return info
        count, section = None, ""
        info = LammpsInfo([])
        for number, line in enumerate(stream):
            body, _separator, comment = line.decode().partition("#")
            if number == 0 or comment:
                read_metadata(body + " " + comment if number == 0 else comment, info)
            values = body.split()
            if values[-2:] == ["atom", "types"]:
                count = int(values[0])
            if values and values[0][0].isalpha():
                section = body.strip()
                if section == "Atoms" and comment.split() and comment.split()[0] in ("atomic", "charge", "full"):
                    info.style = comment.split()[0]
                    info.declared.add("atom_style")
            elif section == "Masses" and len(values) >= 2:
                key, mass = int(values[0]), float(values[1])
                if not np.isfinite(mass) or mass <= 0:
                    raise ValueError("LAMMPS 原子质量需要是有限正数。")
                info.masses[key] = mass
                symbol = comment.split()[0] if comment.split() else ""
                if symbol in atomic_numbers:
                    info.mapping[key] = symbol
            elif section == "Atoms" and values and info.style == "auto":
                info.style = {5: "atomic", 8: "atomic", 6: "charge", 9: "charge", 7: "full", 10: "full"}.get(len(values), "auto")
        if count is None or count <= 0:
            raise ValueError("LAMMPS data 缺少 atom types 声明。")
        info.types = list(range(1, count + 1))
        return info


def read_data(content, mapping, units, style, pbc=(True, True, True)):
    from ase.io.lammpsdata import read_lammps_data
    origin = np.zeros(3)
    for line in content.decode("utf-8").splitlines():
        words = line.partition("#")[0].split()
        if words == ["Atoms"]:
            break
        for axis, label in enumerate("xyz"):
            if words[-2:] == [label + "lo", label + "hi"]:
                origin[axis] = float(words[0])
    atoms = read_lammps_data(StringIO(content.decode("utf-8")),
                            Z_of_type={key: atomic_numbers[symbol] for key, symbol in mapping.items()},
                            sort_by_id=False, units=units, atom_style=None if style == "auto" else style)
    atoms.positions -= convert(origin, "distance", units, "ASE")
    atoms.pbc = pbc
    return from_atoms(atoms, "LAMMPS data; units " + units)


def dump_frame(stream, mapping, units):
    from ase.io.lammpsrun import lammps_data_to_ase_atoms
    step, header, bounds, columns, data, declared = dump_block(stream)
    if declared is not None and declared != units:
        raise ValueError("dump 声明的单位与选择的单位不一致。")
    if len(header) < 3 or any(x not in ("pp", "ff", "ss", "mm", "fs", "fm", "sf", "sm", "mf", "ms") for x in header[-3:]):
        raise ValueError("dump 需要明确的三个方向边界标记。")
    pbc = [x == "pp" for x in header[-3:]]
    if "abc" in header:
        if bounds.shape != (3, 4):
            raise ValueError("general triclinic BOX BOUNDS 需要 3×4 数值。")
        cell, origin = bounds[:, :3], bounds[:, 3]
    else:
        if bounds.shape not in ((3, 2), (3, 3)):
            raise ValueError("BOX BOUNDS 数值无效。")
        xy, xz, yz = bounds[:, 2] if bounds.shape[1] == 3 else (0., 0., 0.)
        low, high = bounds[:, 0].copy(), bounds[:, 1].copy()
        low[:2] -= [min(0, xy, xz, xy + xz), min(0, yz)]
        high[:2] -= [max(0, xy, xz, xy + xz), max(0, yz)]
        x, y, z = high - low
        cell, origin = np.array([[x, 0, 0], [xy, y, 0], [xz, yz, z]]), low
    if "element" not in columns or (mapping and "type" in columns):
        if "type" not in columns:
            raise ValueError("dump 需要 element 或 type 列。")
        types = list(map(int, data[:, columns.index("type")]))
        missing = sorted(set(types) - mapping.keys())
        if missing:
            raise ValueError("未指定元素的 LAMMPS 类型：" + ", ".join(map(str, missing)))
        elements = [mapping[key] for key in types]
        if "element" in columns:
            data = data.astype(object)
            data[:, columns.index("element")] = elements
        else:
            data = np.column_stack((data, elements))
            columns = columns + ["element"]
    atoms = lammps_data_to_ase_atoms(data, columns, cell, origin, pbc=pbc, order=False, units=units)
    coordinates = next((names for names in (("xu", "yu", "zu"), ("x", "y", "z"),
                        ("xsu", "ysu", "zsu"), ("xs", "ys", "zs")) if all(x in columns for x in names)), None)
    if coordinates is None:
        raise ValueError("dump 缺少三维坐标列。")
    points = data[:, [columns.index(x) for x in coordinates]].astype(float)
    scaled = coordinates[0] in ("xs", "xsu")
    points = points @ cell if scaled else points - origin
    if coordinates[0] in ("x", "xs") and all(x in columns for x in ("ix", "iy", "iz")):
        points += data[:, [columns.index(x) for x in ("ix", "iy", "iz")]].astype(int) @ cell
    atoms.positions = convert(points, "distance", units, "ASE")
    atoms.info["timestep"] = step
    return from_atoms(atoms, "LAMMPS dump; units " + units)
