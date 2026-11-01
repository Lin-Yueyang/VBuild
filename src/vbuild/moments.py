"""Read complete on-site spin moments with the corresponding OUTCAR geometry."""

from dataclasses import dataclass, field
import gzip
from pathlib import Path
import re

import numpy as np
from PySide6.QtCore import QThread, Signal

from .bader import same_geometry


def numbers(text):
    return [float(value.replace("D", "E").replace("d", "e")) for value in text.split()]


def matrix(stream, count, columns):
    rows = [numbers(stream.readline()) for _ in range(count)]
    if any(len(row) < columns for row in rows):
        return None
    result = np.array([row[:columns] for row in rows])
    if not np.isfinite(result).all():
        raise ValueError("OUTCAR 含非有限的坐标或磁矩。")
    return result


def moment_table(stream, count):
    rows, columns = [], ()
    for line in stream:
        fields = line.split()
        if not fields or set(line.strip()) == {"-"}:
            continue
        if "# of ion" in line:
            columns = tuple(fields[3:])
            if not columns or columns[-1] != "tot" or len(set(columns)) != len(columns) or any(key not in ("s", "p", "d", "f", "tot") for key in columns):
                raise ValueError("OUTCAR 磁矩轨道列无效，应为 s/p/d/f 中的轨道列及末尾 tot。")
            continue
        if not columns or not fields[0].isdigit() or int(fields[0]) != len(rows) + 1 or len(fields) != len(columns) + 1:
            return None
        values = numbers(" ".join(fields[1:]))
        if not values or not np.isfinite(values).all():
            raise ValueError("OUTCAR 的逐原子磁矩不是有效数值。")
        rows.append(values)
        if len(rows) == count:
            return columns, np.array(rows)
    return None


@dataclass
class MomentData:
    path: Path
    symbols: tuple
    positions: np.ndarray
    cell: np.ndarray
    moments: np.ndarray
    step: int
    saxis: tuple
    component: str = "scalar"
    decimals: int = 2
    hidden_elements: set = field(default_factory=set)
    orbitals: dict = field(default_factory=dict)
    orbital: str = "tot"
    total_moment: np.ndarray | None = None

    @property
    def orbital_names(self):
        return ("tot", *self.orbitals)

    def matches(self, structure):
        return self.symbols == structure.symbols and same_geometry(self.positions, self.cell, structure)

    def values(self):
        values = self.moments if self.orbital == "tot" else self.orbitals[self.orbital]
        if self.component == "norm":
            return np.linalg.norm(values, axis=1)
        return values[:, 0 if self.component == "scalar" else "xyz".index(self.component)]

    def labels(self):
        prefix = "|m|" if self.component == "norm" else "m" if self.component == "scalar" else "m" + self.component
        if self.orbital != "tot":
            prefix += "(" + self.orbital + ")"
        return ["" if symbol in self.hidden_elements else f"{prefix} {value:+.{self.decimals}f} μB"
                for symbol, value in zip(self.symbols, self.values())]

    def summary(self, visible=None):
        keep = np.array([symbol not in self.hidden_elements for symbol in self.symbols])
        if visible is not None:
            keep &= visible
        values = self.moments if self.orbital == 'tot' else self.orbitals[self.orbital]
        selected = values[keep].sum(axis=0)
        def format_value(vector):
            if vector is None:
                return '未提供'
            value = np.linalg.norm(vector) if self.component == 'norm' else vector[0 if self.component == 'scalar' else 'xyz'.index(self.component)]
            return f'{0.0 if round(float(value), self.decimals) == 0 else value:.{self.decimals}f} μB'
        return ('已显示原子的磁矩合计：' + format_value(selected) +
                '\n全部原子的磁矩合计：' + format_value(values.sum(axis=0)) +
                '\n体系自旋总磁矩：' + format_value(self.total_moment))


def read_moments(source, stop=lambda: False):
    path = Path(source)
    if not path.is_file():
        entries = {entry.name.lower(): entry for entry in path.iterdir() if entry.is_file()}
        path = entries.get("outcar", entries.get("outcar.gz"))
        if path is None:
            raise ValueError("当前结构目录中没有检测到 OUTCAR（或 OUTCAR.gz）。")
    opener = gzip.open if path.suffix.lower() == ".gz" else open
    species, symbols, count = [], (), 0
    cell, positions, last = None, None, None
    components, step, noncollinear, saxis = {}, 0, False, (0., 0., 1.)
    total = None
    iteration = re.compile(r"Iteration\s+(\d+)\s*\(")
    with opener(path, "rt", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            if stop():
                return None
            if line.lstrip().startswith("vasp."):
                species, symbols, count = [], (), 0
                cell, positions, components, step = None, None, {}, 0
                noncollinear, saxis = False, (0., 0., 1.)
                total = None
            elif "VRHFIN" in line and not symbols:
                species.append(line.split("=", 1)[1].split(":", 1)[0].strip())
            elif "ions per type" in line:
                counts = [int(value) for value in line.split("=", 1)[1].split()]
                if len(species) < len(counts):
                    raise ValueError("OUTCAR 缺少对应元素的 VRHFIN，无法核对原子顺序。")
                symbols = tuple(symbol for symbol, size in zip(species, counts) for _ in range(size))
                count = len(symbols)
            elif "LNONCOLLINEAR" in line and "=" in line:
                noncollinear = line.split("=", 1)[1].split()[0].upper().startswith("T")
            elif "SAXIS" in line and "=" in line:
                saxis = tuple(numbers(" ".join(line.split("=", 1)[1].split()[:3])))
            elif "Iteration" in line:
                match = iteration.search(line)
                if match is not None and int(match[1]) != step:
                    if step:
                        positions = None
                    step, components = int(match[1]), {}
                    total = None
            elif 'number of electron' in line and 'magnetization' in line:
                text = line.split('magnetization', 1)[1].lstrip(' =').strip()
                number = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][+-]?\d+)?'
                if re.fullmatch(number + (r'(?:\s+' + number + r'){2}' if noncollinear else ''), text):
                    values = np.array(numbers(text))
                    if np.isfinite(values).all():
                        total = values
                continue
            elif "direct lattice vectors" in line:
                cell = matrix(stream, 3, 3)
                if cell is None:
                    break
            elif "position of ions in cartesian" in line:
                positions = matrix(stream, count, 3)
            elif "POSITION" in line and "TOTAL-FORCE" in line:
                stream.readline()
                values = matrix(stream, count, 6)
                if values is None:
                    break
                positions = values[:, :3]
            elif line.strip() in ("magnetization (x)", "magnetization (y)", "magnetization (z)"):
                axis = line.strip()[-2]
                if axis == "x":
                    components = {}
                values = moment_table(stream, count)
                if values is None:
                    break
                components[axis] = values
            else:
                continue
            axes = "xyz" if noncollinear else "x"
            if positions is not None and cell is not None and count and all(axis in components for axis in axes):
                columns = components["x"][0]
                if any(components[axis][0] != columns for axis in axes):
                    raise ValueError("OUTCAR 同一组 x/y/z 磁矩的轨道列不一致。")
                channels = {key: np.column_stack([components[axis][1][:, index] for axis in axes])
                            for index, key in enumerate(columns)}
                last = MomentData(path, symbols, positions.copy(), cell.copy(),
                                  channels.pop("tot"), step, saxis,
                                  component="norm" if noncollinear else "scalar", orbitals=channels,
                                  total_moment=None if total is None else total.copy())
    if last is None:
        raise ValueError("OUTCAR 没有完整的逐原子磁矩与对应结构。请确认启用了自旋及 LORBIT 输出；MAGMOM 初值和总磁矩不作为原子磁矩。")
    return last


class MomentTask(QThread):
    ready = Signal(object)
    failed = Signal(str)

    def __init__(self, source, parent):
        super().__init__(parent)
        self.source = source

    def run(self):
        try:
            data = read_moments(self.source, self.isInterruptionRequested)
            if data is not None and not self.isInterruptionRequested():
                self.ready.emit(data)
        except (OSError, ValueError, EOFError) as error:
            if not self.isInterruptionRequested():
                self.failed.emit(str(error))
