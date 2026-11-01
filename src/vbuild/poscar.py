"""Read VASP structures; export current atoms and freshly sampled velocities."""

from dataclasses import dataclass
from pathlib import Path
import re

import numpy as np
from ase.data import atomic_numbers

from .model import Structure, Velocities
from .thermal import generate, temperature as kinetic_temperature


def numbers(line: str) -> list[float]:
    return [float(value.replace("D", "E").replace("d", "e"))
            for value in line.split()[:3]]


def equal_optional(first: np.ndarray | None, second: np.ndarray | None) -> bool:
    if first is None or second is None:
        return first is second
    return np.array_equal(first, second)


@dataclass
class PoscarDocument:
    structure: Structure
    source: bytes
    lines: list[str]
    position_start: int
    selective_line: int | None
    mode: str
    scales: np.ndarray
    restart: bool
    original_positions: np.ndarray
    original_free: np.ndarray | None
    original_cell: np.ndarray
    original_symbols: tuple[str, ...]
    original_labels: tuple[str, ...]
    original_temperature: float | None
    original_pending: bool
    input_velocities: Velocities | None
    unsaved: bool = False

    @classmethod
    def read(cls, path: str | Path) -> "PoscarDocument":
        return cls.from_bytes(Path(path).read_bytes())

    @classmethod
    def from_bytes(cls, source: bytes) -> "PoscarDocument":
        lines = source.decode("utf-8-sig").splitlines(keepends=True)
        if len(lines) < 8:
            raise ValueError("POSCAR 缺少必要的头部或原子坐标。")

        scale = np.array([float(value) for value in lines[1].split()])
        lattice = np.array([numbers(line) for line in lines[2:5]])
        if lattice.shape != (3, 3) or not np.isfinite(lattice).all():
            raise ValueError("晶胞必须由三个有限的三维向量组成。")
        volume = abs(np.linalg.det(lattice))
        if volume == 0:
            raise ValueError("晶胞体积不能为零。")
        if scale.size == 1:
            value = scale[0]
            effective = np.cbrt(-value / volume) if value < 0 else value
            scales = np.repeat(effective, 3)
        elif scale.size == 3:
            scales = scale
        else:
            raise ValueError("缩放因子必须是一个数值或三个正数。")
        if not np.isfinite(scales).all() or (scales <= 0).any():
            raise ValueError("有效缩放因子必须为有限正数。")
        cell = lattice * scales

        groups = lines[5].split()
        if all(token.isdigit() for token in groups):
            raise ValueError("第一阶段需要 VASP 5/6 的元素名行；不猜测 VASP 4 的元素。")
        counts = [int(value) for value in lines[6].split()]
        if len(groups) != len(counts) or any(count <= 0 for count in counts):
            raise ValueError("元素名与原子数量必须对应，数量必须为正。")
        # Keep the original labels in the text; display their element portion.
        elements = [label.split("/")[0].split("_")[0] for label in groups]
        if any(element not in atomic_numbers or element == "X" for element in elements):
            raise ValueError("元素名无法识别；不使用猜测的元素替代。")
        symbols = tuple(element for element, count in zip(elements, counts)
                        for _ in range(count))
        labels = tuple(group for group, count in zip(groups, counts) for _ in range(count))

        cursor = 7
        selective_line = None
        if lines[cursor].lstrip().lower().startswith("s"):
            selective_line = cursor
            cursor += 1
        if cursor >= len(lines):
            raise ValueError("缺少坐标模式和原子坐标。")
        coordinate_mode = lines[cursor].strip().lower()
        if not coordinate_mode or coordinate_mode[0] not in "dck":
            raise ValueError("坐标模式需要 Direct 或 Cartesian。")
        mode = "direct" if coordinate_mode[0] == "d" else "cartesian"
        start = cursor + 1
        end = start + len(symbols)
        if end > len(lines):
            raise ValueError("原子坐标行数少于头部声明的数量。")
        coordinates = np.array([numbers(line) for line in lines[start:end]])
        if coordinates.shape != (len(symbols), 3) or not np.isfinite(coordinates).all():
            raise ValueError("每个原子需要三个有限的坐标。")
        positions = coordinates @ cell if mode == "direct" else coordinates * scales

        free = None
        if selective_line is not None:
            flags = [line.split()[3:6] for line in lines[start:end]]
            if any(len(row) != 3 or any(flag.upper() not in ("T", "F")
                                      for flag in row) for row in flags):
                raise ValueError("Selective dynamics 必须为每个原子提供三个 T/F 标记。")
            free = np.array([[flag.upper() == "T" for flag in row] for row in flags])

        cursor = end
        restart = False
        if cursor < len(lines) and lines[cursor].lstrip().lower().startswith("lattice velocities"):
            # Eight lines: header, initialization state, velocities and lattice.
            if cursor + 8 > len(lines):
                raise ValueError("晶胞速度区块不完整。")
            restart = True
            cursor += 8

        velocities = None
        if any(line.strip() for line in lines[cursor:]):
            velocity_mode = lines[cursor].strip().lower()
            if not velocity_mode or velocity_mode[0] in "dck":
                vmode = "direct" if velocity_mode.startswith("d") else "cartesian"
                cursor += 1
                vectors = np.array([numbers(line) for line in lines[cursor:cursor + len(symbols)]])
                if vectors.shape != (len(symbols), 3) or not np.isfinite(vectors).all():
                    raise ValueError("原子速度区块不完整或包含无效数值。")
                velocities = Velocities(vectors, vmode)
                cursor += len(symbols)
            restart = restart or any(line.strip() for line in lines[cursor:])

        pending = velocities is not None and velocities.mode == "direct"
        temperature = (kinetic_temperature(symbols, velocities.values, cell, free)
                       if velocities is not None and not pending else None)
        structure = Structure(symbols, cell, positions, free, temperature, labels,
                              tuple(range(len(symbols))), pending)
        return cls(structure, source, lines, start, selective_line, mode, scales,
                   restart, positions.copy(), None if free is None else free.copy(),
                   cell.copy(), symbols, labels, temperature, pending, velocities)

    @property
    def dirty(self) -> bool:
        return (self.unsaved or not np.array_equal(self.structure.cell, self.original_cell)
                or not np.array_equal(self.structure.positions, self.original_positions)
                or not equal_optional(self.structure.free, self.original_free)
                or self.structure.symbols != self.original_symbols
                or self.structure.labels != self.original_labels
                or self.structure.origins != tuple(range(len(self.original_symbols)))
                or self.structure.temperature != self.original_temperature
                or self.structure.temperature_pending != self.original_pending)

    def input_temperature(self, timestep: float | None = None) -> float:
        if self.input_velocities is None:
            raise ValueError("原文件没有速度可换算；请直接指定导出温度。")
        velocities = self.input_velocities.values
        if self.input_velocities.mode == "direct":
            if timestep is None or not np.isfinite(timestep) or timestep <= 0:
                raise ValueError("Direct 速度换算需要有效的 POTIM（fs），不能假定时间步。")
            velocities = velocities @ self.original_cell / timestep
        return kinetic_temperature(self.original_symbols, velocities, self.original_cell, self.original_free)

    def ensure_editable(self) -> None:
        if self.restart:
            raise ValueError("此文件含 MD 续算或未识别的尾部数据；当前阶段只允许查看和原样保存。")

    def to_bytes(self, *, regenerate: bool = True, seed: int | None = None) -> bytes:
        structure = self.structure
        count = len(structure.symbols)
        if structure.cell is None or structure.cell.shape != (3, 3) or not np.isfinite(structure.cell).all() or abs(np.linalg.det(structure.cell)) < 1e-12:
            raise ValueError("导出晶胞需要三个有限且线性独立的向量。")
        cell_changed = not np.array_equal(structure.cell, self.original_cell)
        if structure.positions.shape != (count, 3) or not np.isfinite(structure.positions).all():
            raise ValueError("坐标数量或数值无效。")
        if len(structure.labels) != count or len(structure.origins) != count:
            raise ValueError("原子元素标签或来源映射与数量不一致。")
        for index, origin in enumerate(structure.origins):
            if origin is not None and (origin < 0 or origin >= len(self.original_symbols)
                    or structure.symbols[index] != self.original_symbols[origin]
                    or structure.labels[index] != self.original_labels[origin]):
                raise ValueError("已有原子的元素/来源不匹配；请通过增删原子操作改变元素。")
        if structure.free is not None and structure.free.shape != structure.positions.shape:
            raise ValueError("约束数量与原子数量不一致。")
        if self.restart:
            if self.dirty:
                self.ensure_editable()
            return self.source
        if regenerate and structure.temperature_pending:
            raise ValueError("原文件为 Direct 速度；请在“温度”中填写 POTIM 换算，或明确设置导出温度。")
        if not self.dirty and (not regenerate or structure.temperature is None):
            return self.source
        topology_changed = structure.origins != tuple(range(len(self.original_symbols)))
        if not regenerate and (topology_changed or cell_changed) and self.input_velocities is not None:
            raise ValueError("晶胞或原子数量/来源变化后必须按温度重新生成速度。")

        newline = "\r\n" if "\r\n" in self.lines[0] else "\n"
        prefix = self.lines[:self.position_start].copy()
        if cell_changed:
            prefix[1] = "1.0" + newline
            prefix[2:5] = [" ".join(format(value, ".17g") for value in row) + newline for row in structure.cell]
        if topology_changed:
            labels, counts = [], []
            for label in structure.labels:
                if labels and label == labels[-1]:
                    counts[-1] += 1
                else:
                    labels.append(label)
                    counts.append(1)
            prefix[5] = " ".join(labels) + newline
            prefix[6] = " ".join(map(str, counts)) + newline
        if self.selective_line is None and structure.free is not None:
            prefix.insert(len(prefix) - 1, "Selective dynamics" + newline)
        elif self.selective_line is not None and structure.free is None:
            prefix.pop(self.selective_line)
        coordinates = (structure.positions @ np.linalg.inv(structure.cell)
                       if self.mode == "direct" else structure.positions / (1 if cell_changed else self.scales))
        result = prefix
        for index, values in enumerate(coordinates):
            origin = structure.origins[index]
            original = None if origin is None else self.lines[self.position_start + origin]
            unchanged_position = not cell_changed and origin is not None and np.array_equal(structure.positions[index], self.original_positions[origin])
            unchanged_free = (structure.free is None and self.original_free is None
                              or origin is not None and structure.free is not None and self.original_free is not None
                              and np.array_equal(structure.free[index], self.original_free[origin]))
            if unchanged_position and unchanged_free:
                result.append(original if index == count - 1 or original.endswith(("\n", "\r")) else original + newline)
                continue
            suffix = ""
            if original is not None:
                fields = list(re.finditer(r"\S+", original))
                consumed = 6 if self.selective_line is not None else 3
                suffix = original[fields[consumed - 1].end():].rstrip("\r\n")
            row = " ".join(format(value, ".17g") for value in values)
            if structure.free is not None:
                row += " " + " ".join("T" if flag else "F" for flag in structure.free[index])
            result.append(row + suffix + newline)
        if regenerate and structure.temperature is not None:
            velocities = generate(structure.symbols, structure.cell, structure.free, structure.temperature, seed)
            if not result[-1].endswith(("\n", "\r")):
                result[-1] += newline
            result.append("Cartesian" + newline)
            result.extend(" ".join(format(value, ".17g") for value in row) + newline for row in velocities)
        elif not regenerate or self.input_velocities is None:
            result.extend(self.lines[self.position_start + len(self.original_symbols):])
        encoding = "utf-8-sig" if self.source.startswith(b"\xef\xbb\xbf") else "utf-8"
        return "".join(result).encode(encoding)

    def write(self, path: str | Path, *, overwrite: bool = False,
              regenerate: bool = True, seed: int | None = None) -> None:
        content = self.to_bytes(regenerate=regenerate, seed=seed)
        with Path(path).open("wb" if overwrite else "xb") as stream:
            stream.write(content)

    def summary(self) -> dict:
        free = self.structure.free
        velocities = self.input_velocities
        return {
            "atoms": len(self.structure.symbols),
            "elements": list(dict.fromkeys(self.structure.symbols)),
            "position_mode": self.mode,
            "selective_dynamics": free is not None,
            "fully_fixed": 0 if free is None else int((~free).all(axis=1).sum()),
            "partly_fixed": 0 if free is None else int(((~free).any(axis=1) & free.any(axis=1)).sum()),
            "velocities": velocities is not None,
            "velocity_mode": None if velocities is None else velocities.mode,
            "temperature_K": self.structure.temperature,
            "temperature_pending": self.structure.temperature_pending,
            "restart_data": self.restart,
            "dirty": self.dirty,
        }
