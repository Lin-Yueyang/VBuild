"""Explicit structure conversion; imported observations are separate from geometry."""

from copy import deepcopy
from dataclasses import dataclass, field
from io import BytesIO, StringIO
from pathlib import Path

import numpy as np
from ase import Atoms, units
from ase.data import atomic_numbers

from .draft import DraftDocument, validate_cell
from .model import Structure, Velocities
from .thermal import temperature
from .writing import cif_columns, data_columns, group_order, numeric_rows, vasp_bytes


@dataclass
class FrameDocument(DraftDocument):
    observations: dict = field(default_factory=dict)
    forces: np.ndarray | None = None
    extras: dict = field(default_factory=dict)


def from_atoms(atoms, title):
    symbols = tuple(atoms.get_chemical_symbols())
    if not symbols or any(symbol not in atomic_numbers or symbol == "X" for symbol in symbols):
        raise ValueError("文件需要明确的真实元素，不能以类型编号猜测元素。")
    positions = atoms.positions.copy()
    if not np.isfinite(positions).all():
        raise ValueError("原子坐标必须是有限数值。")
    cell = atoms.cell.array.copy() if atoms.cell.rank else None
    if cell is not None:
        validate_cell(cell)
    if cell is None and atoms.pbc.any():
        raise ValueError("周期边界需要完整的三维晶胞。")
    free = None
    if atoms.constraints:
        from ase.constraints import FixAtoms, FixScaled
        free = np.ones(positions.shape, dtype=bool)
        for constraint in atoms.constraints:
            if isinstance(constraint, FixAtoms):
                free[constraint.index] = False
            elif isinstance(constraint, FixScaled):
                free[constraint.index] = ~constraint.mask
            else:
                raise ValueError("导入约束不能等价转换为晶胞方向固定标记。")
    value = None if "temperature" not in atoms.info else float(atoms.info["temperature"])
    if atoms.has("momenta"):
        basis = np.eye(3) if cell is None else cell
        masses = atoms.get_masses()
        if not np.isfinite(masses).all() or np.any(masses <= 0):
            raise ValueError("速度换温度需要每个原子的质量为有限正数。")
        value = temperature(symbols, atoms.get_velocities() * units.fs, basis, free, weights=masses)
    if value is not None and (not np.isfinite(value) or value < 0):
        raise ValueError("文件温度需要有限非负数，单位 K。")
    structure = Structure(symbols, cell, positions, free, value, symbols, tuple(range(len(symbols))),
                          periodic=tuple(bool(x) for x in atoms.pbc))
    results = {} if atoms.calc is None else atoms.calc.results
    forces = results.get("forces", atoms.arrays.get("forces"))
    if forces is not None:
        forces = np.asarray(forces, dtype=float).copy()
        if forces.shape != positions.shape or not np.isfinite(forces).all():
            raise ValueError("导入力需要每个原子三个有限分量。")
    observations = {key: float(results[key]) for key in ("energy", "free_energy") if key in results}
    for key in ("energy", "free_energy", "temperature", "time", "timestep"):
        if key in atoms.info:
            observations[key] = float(atoms.info[key])
    if not all(np.isfinite(x) for x in observations.values()):
        raise ValueError("文件能量/温度/时间含无效数值。")
    if forces is not None:
        observations["max_force"] = float(np.linalg.norm(forces, axis=1).max())
    extras = {key: value.copy() for key, value in atoms.arrays.items() if key not in ("positions", "numbers")}
    extras["metadata"] = deepcopy(atoms.info)
    document = FrameDocument(structure, title, unsaved=False, observations=observations, forces=forces, extras=extras)
    if atoms.has("momenta"):
        document.input_velocities = Velocities(atoms.get_velocities() * units.fs, "cartesian")
    return document


def read_cif(content, title):
    from ase.io import read
    documents = []
    for image in read(BytesIO(content), format="cif", index=":"):
        occupancy = image.info.get("occupancy", {})
        if any(len(site) != 1 or any(abs(value - 1) > 1e-8 for value in site.values()) for site in occupancy.values()):
            raise ValueError("CIF 含部分占位/混合占位，暂不能转成确定元素结构。")
        documents.append(from_atoms(image, title))
    if not documents:
        raise ValueError("CIF 中没有结构。")
    return documents


def output_kind(path):
    suffix = Path(path).suffix.lower()
    from .volume import grid_kind
    if grid_kind(path) is not None or Path(path).name.lower().startswith("vasprun.xml"):
        raise ValueError("此格式仅支持导入；请导出为 POSCAR、XYZ、CIF 或 LAMMPS data。")
    if suffix == ".xyz":
        return "xyz"
    if suffix == ".cif":
        return "cif"
    if suffix in (".data", ".lmp", ".lammps") or Path(path).name.startswith("data."):
        return "lammps-data"
    if suffix in (".xml", ".extxyz", ".entxyz", ".dump", ".lammpstrj"):
        raise ValueError("此格式仅支持导入；请导出为 POSCAR、XYZ、CIF 或 LAMMPS data。")
    return "vasp"


def export_velocities(document, regenerate):
    from .poscar import PoscarDocument
    from .thermal import generate
    structure = document.structure
    if regenerate:
        if structure.temperature_pending:
            raise ValueError("Direct 速度需先换算温度，或在导出窗口明确设置温度。")
        if structure.temperature is not None:
            return Velocities(generate(structure.symbols, structure.cell, structure.free, structure.temperature), 'cartesian')
        return None
    velocities = deepcopy(document.input_velocities)
    if velocities is not None:
        if isinstance(document, PoscarDocument):
            if any(origin is None for origin in structure.origins):
                raise ValueError("新增原子没有原始速度，请勾选“重新赋予温度”。")
            if velocities.mode == 'direct' and not np.array_equal(structure.cell, document.original_cell):
                raise ValueError("晶胞变化后不能保留原始 Direct 速度，请勾选“重新赋予温度”。")
            velocities.values = velocities.values[list(structure.origins)]
        if velocities.values.shape != structure.positions.shape:
            raise ValueError("原始速度与当前原子数量不一致，请勾选“重新赋予温度”。")
    return velocities


def serialize(document, kind, *, regenerate=True, coordinate_mode=None):
    from .poscar import PoscarDocument
    structure = document.structure
    if kind != "xyz" and structure.cell is None:
        raise ValueError("当前结构没有晶胞；只能导出 XYZ，不能补造晶胞。")
    if not structure.symbols:
        raise ValueError("空结构尚无原子，请放置原子后导出。")
    count = len(structure.symbols)
    if structure.positions.shape != (count, 3) or not np.isfinite(structure.positions).all():
        raise ValueError("坐标数量或数值无效。")
    if len(structure.labels) != count or len(structure.origins) != count:
        raise ValueError("原子元素标签或来源映射与数量不一致。")
    if structure.free is not None and structure.free.shape != structure.positions.shape:
        raise ValueError("约束数量与原子数量不一致。")
    if structure.cell is not None:
        validate_cell(structure.cell)
    if kind == "vasp":
        if not all(structure.pbc):
            raise ValueError("VASP 不能保存非周期或部分周期边界。")
        if document.restart:
            if coordinate_mode is not None and coordinate_mode != document.mode:
                raise ValueError('文件含续算数据，VASP 导出需保留原始坐标格式。')
            return document.to_bytes(regenerate=regenerate)
        velocities = export_velocities(document, regenerate)
        preferred = document.original_labels if isinstance(document, PoscarDocument) else document.original.labels
        title = document.lines[0].strip() if isinstance(document, PoscarDocument) else document.title
        return vasp_bytes(structure, title, preferred=preferred, velocities=velocities,
                          coordinate_mode='direct' if coordinate_mode is None else coordinate_mode)
    if kind == "xyz":
        from .trajectory import XyzDocument
        title = document.comment if isinstance(document, XyzDocument) else "VBuild structure; Cartesian angstrom"
        lines = [str(len(structure.symbols)), title]
        width = max(map(len, structure.symbols))
        lines.extend(symbol.ljust(width) + "  " + row for symbol, row in zip(structure.symbols, numeric_rows(structure.positions)))
        return ("\n".join(lines) + "\n").encode()
    cell = structure.cell.copy()
    if np.linalg.det(cell) < 0:
        if not all(structure.pbc):
            raise ValueError("非全周期的左手晶胞，请先调整为右手基准再导出 CIF/data。")
        # Reverse a lattice vector, keeping Cartesian atoms intact; prevents an improper rotation of chiral molecules.
        cell[2] *= -1
    atoms = Atoms(symbols=structure.symbols, positions=structure.positions, cell=cell, pbc=structure.pbc)
    from ase.io import write
    preferred = document.original_symbols if isinstance(document, PoscarDocument) else document.original.symbols
    if kind == "cif":
        if not all(structure.pbc):
            raise ValueError("CIF 晶体结构要求三个方向均周期。")
        stream = BytesIO()
        order, _names, _counts = group_order(structure.symbols, preferred)
        atoms = atoms[order]
        write(stream, atoms, format="cif")
        return cif_columns(stream.getvalue(), count)
    if kind == "lammps-data":
        stream = StringIO()
        velocities = export_velocities(document, regenerate)
        if velocities is not None:
            if velocities.mode != "cartesian":
                raise ValueError("LAMMPS 速度导出需要匹配原子数量的笛卡尔速度，请重新赋温度。")
            atoms.set_velocities(velocities.values / units.fs)
        write(stream, atoms, format="lammps-data", atom_style="atomic", units="metal", masses=True,
              specorder=group_order(structure.symbols, preferred)[1], velocities=atoms.has("momenta"))
        return data_columns(stream.getvalue(), count)
    raise ValueError("不支持的导出格式：" + kind)
