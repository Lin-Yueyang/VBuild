"""New in-memory structures, including genuinely empty and nonperiodic drafts."""

from copy import deepcopy
from dataclasses import dataclass, field

import numpy as np

from .model import Structure
from .poscar import equal_optional
from .thermal import generate


def validate_cell(cell):
    if cell.shape != (3, 3) or not np.isfinite(cell).all() or abs(np.linalg.det(cell)) < 1e-10:
        raise ValueError("晶胞需要三个有限且线性独立的向量，体积不能为零。")


@dataclass
class DraftDocument:
    structure: Structure
    title: str = "VBuild structure"
    unsaved: bool = True
    restart: bool = False
    input_velocities: object = None
    original: Structure = field(init=False)

    def __post_init__(self):
        self.original = deepcopy(self.structure)

    @classmethod
    def blank(cls, cell):
        if cell is not None:
            validate_cell(cell)
        return cls(Structure((), cell, np.empty((0, 3)), None, None, (), ()), "VBuild empty structure")

    @property
    def dirty(self):
        first, second = self.structure, self.original
        return (self.unsaved or first.symbols != second.symbols or first.labels != second.labels
                or not equal_optional(first.cell, second.cell) or not equal_optional(first.free, second.free)
                or not np.array_equal(first.positions, second.positions) or first.temperature != second.temperature
                or first.pbc != second.pbc)

    @property
    def original_free(self):
        return self.original.free

    @property
    def original_temperature(self):
        return self.original.temperature

    def ensure_editable(self):
        """Drafts have no unrecognized source data to protect."""

    def to_bytes(self, *, regenerate=True, seed=None):
        structure = self.structure
        count = len(structure.symbols)
        if count == 0:
            raise ValueError("空结构尚无原子，请放置原子后导出。")
        if structure.positions.shape != (count, 3) or not np.isfinite(structure.positions).all():
            raise ValueError("坐标数量或数值无效。")
        if len(structure.labels) != count or len(structure.origins) != count:
            raise ValueError("原子元素标签或来源映射与数量不一致。")
        if structure.free is not None and structure.free.shape != structure.positions.shape:
            raise ValueError("约束数量与原子数量不一致。")
        if structure.cell is None:
            rows = [str(count), self.title]
            rows.extend(symbol + " " + " ".join(format(value, ".17g") for value in position)
                        for symbol, position in zip(structure.symbols, structure.positions))
        else:
            validate_cell(structure.cell)
            labels, counts = [], []
            for label in structure.labels:
                if labels and label == labels[-1]:
                    counts[-1] += 1
                else:
                    labels.append(label)
                    counts.append(1)
            rows = [self.title, "1.0", *(" ".join(format(value, ".17g") for value in row) for row in structure.cell),
                    " ".join(labels), " ".join(map(str, counts))]
            if structure.free is not None:
                rows.append("Selective dynamics")
            rows.append("Cartesian")
            for index, position in enumerate(structure.positions):
                row = " ".join(format(value, ".17g") for value in position)
                if structure.free is not None:
                    row += " " + " ".join("T" if flag else "F" for flag in structure.free[index])
                rows.append(row)
            if regenerate and structure.temperature is not None:
                velocities = generate(structure.symbols, structure.cell, structure.free, structure.temperature, seed)
                rows.extend(["Cartesian", *(" ".join(format(value, ".17g") for value in row) for row in velocities)])
        return ("\n".join(rows) + "\n").encode("utf-8")
