"""Read existing Bader results; baselines come from the calculation's POTCAR."""

from dataclasses import dataclass, field
from pathlib import Path
import re

import numpy as np
from ase.data import atomic_numbers


def same_geometry(positions, cell, structure, tolerance=.002):
    if positions.shape != structure.positions.shape or (cell is None) != (structure.cell is None):
        return False
    if cell is not None and not np.allclose(cell, structure.cell, atol=1e-6, rtol=1e-6):
        return False
    delta = structure.positions - positions
    if cell is not None:
        fraction = delta @ np.linalg.inv(cell)
        fraction[:, structure.pbc] -= np.rint(fraction[:, structure.pbc])
        delta = fraction @ cell
    return bool(np.all(np.linalg.norm(delta, axis=1) <= tolerance))


def potcar_values(path, structure):
    species, values = [], []
    symbol = None
    with path.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            if "VRHFIN" in line:
                symbol = line.split("=", 1)[1].split(":", 1)[0].strip()
            if "ZVAL" in line:
                match = re.search(r"ZVAL\s*=\s*([\d.EeDd+-]+)", line)
                if match is None or symbol not in atomic_numbers:
                    raise ValueError("POTCAR 缺少有效的 VRHFIN / ZVAL。")
                species.append(symbol)
                values.append(float(match.group(1).replace("D", "E").replace("d", "e")))
                symbol = None
    # Consecutive POSCAR species blocks can use different potentials of one element.
    boundaries = [0] + [i for i in range(1, len(structure.labels)) if structure.labels[i] != structure.labels[i-1]] + [len(structure.labels)]
    expected = [structure.symbols[i] for i in boundaries[:-1]]
    if species != expected or not np.isfinite(values).all() or any(value <= 0 for value in values):
        raise ValueError("POTCAR 元素顺序/组数与当前结构不一致，不能确定各原子的 ZVAL。")
    return np.repeat(values, np.diff(boundaries))


@dataclass
class BaderData:
    path: Path
    rows: np.ndarray
    symbols: tuple
    positions: np.ndarray
    cell: np.ndarray | None
    neutral: np.ndarray | None
    baseline: str
    files: tuple
    summary: str
    mode: str = "valence"
    decimals: int = 2
    hidden_elements: set = field(default_factory=set)

    def matches(self, structure):
        return structure.symbols == self.symbols and same_geometry(self.positions, self.cell, structure)

    def charges(self):
        if self.mode == "electrons":
            return self.rows[:, 4]
        baseline = (np.array([atomic_numbers[symbol] for symbol in self.symbols])
                    if self.mode == "all" else self.neutral)
        return None if baseline is None else baseline - self.rows[:, 4]

    def labels(self):
        charges = self.charges()
        if charges is None:
            return None
        return ["" if symbol in self.hidden_elements else
                f"{value:+.{self.decimals}f} e" if self.mode != "electrons" else f"{value:.{self.decimals}f} e⁻"
                for symbol, value in zip(self.symbols, charges)]


def read_bader(directory, structure):
    directory = Path(directory)
    entries = {path.name.lower(): path for path in directory.iterdir() if path.is_file()}
    path = entries.get("acf.dat")
    if path is None:
        raise ValueError("没检测到 ACF.dat。请将 Bader 结果放在当前结构所在文件夹。")
    rows, summary = [], []
    with path.open(encoding="utf-8-sig") as stream:
        for line in stream:
            fields = line.split()
            if fields and fields[0].isdigit():
                if len(fields) != 7:
                    raise ValueError("ACF.dat 原子行需要编号、xyz、CHARGE、MIN DIST 和 ATOMIC VOL 七列。")
                rows.append([float(value.replace("D", "E").replace("d", "e")) for value in fields])
            elif ":" in line:
                summary.append(line.strip())
    rows = np.asarray(rows, dtype=float)
    count = len(structure.symbols)
    if rows.shape != (count, 7) or not np.isfinite(rows).all():
        raise ValueError("ACF.dat 原子数与当前结构不一致，或数据未写完/含无效数值。")
    if not np.array_equal(rows[:, 0], np.arange(1, count + 1)):
        raise ValueError("ACF.dat 原子编号需要按顺序从 1 到原子总数。")
    if not same_geometry(rows[:, 1:4], structure.cell, structure):
        raise ValueError("ACF.dat 的原子坐标/顺序与当前结构不一致，请打开对应计算的最终结构。")
    potcar = entries.get("potcar")
    neutral = None if potcar is None else potcar_values(potcar, structure)
    files = tuple(entries[name].name for name in ("acf.dat", "bcf.dat", "avf.dat", "atomvolumes.dat") if name in entries)
    return BaderData(path, rows, structure.symbols, structure.positions.copy(),
                     None if structure.cell is None else structure.cell.copy(), neutral,
                     "缺少 POTCAR，价电子净电荷不可用" if neutral is None else "POTCAR / ZVAL", files,
                     "\n".join(summary))
