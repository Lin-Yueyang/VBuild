"""Structure data in Cartesian angstroms; T means a free lattice direction."""

from dataclasses import dataclass

import numpy as np
from ase.data import atomic_numbers


@dataclass
class Velocities:
    values: np.ndarray
    mode: str


@dataclass
class Structure:
    symbols: tuple[str, ...]
    cell: np.ndarray | None
    positions: np.ndarray
    free: np.ndarray | None
    temperature: float | None
    labels: tuple[str, ...]
    origins: tuple[int | None, ...]
    temperature_pending: bool = False
    periodic: tuple[bool, bool, bool] | None = None

    @property
    def pbc(self):
        return (False, False, False) if self.cell is None else (True, True, True) if self.periodic is None else self.periodic

    def translate(self, indices: list[int], delta: np.ndarray) -> None:
        validate_indices(indices, len(self.symbols))
        delta = np.asarray(delta, dtype=float)
        if delta.shape != (3,) or not np.isfinite(delta).all():
            raise ValueError("位移必须是三个有限数值，单位为 Å。")
        self.positions[indices] += delta

    def set_free(self, indices: list[int], flags: list[bool]) -> None:
        validate_indices(indices, len(self.symbols))
        if len(flags) != 3:
            raise ValueError("约束必须包含 a、b、c 三个方向。")
        if self.free is None:
            self.free = np.ones((len(self.symbols), 3), dtype=bool)
        self.free[indices] = flags

    def set_temperature(self, value: float | None) -> None:
        if value is not None and (not np.isfinite(value) or value < 0):
            raise ValueError("温度必须是有限非负数，单位为 K。")
        self.temperature = value
        self.temperature_pending = False

    def set_fixed(self, indices: list[int], flags: list[bool | None]) -> None:
        validate_indices(indices, len(self.symbols))
        if len(flags) != 3:
            raise ValueError("固定标记必须包含 a、b、c 三个方向。")
        if self.free is None and not any(flag for flag in flags if flag is not None):
            return
        if self.free is None:
            self.free = np.ones((len(self.symbols), 3), dtype=bool)
        for axis, fixed in enumerate(flags):
            if fixed is not None:
                self.free[indices, axis] = not fixed

    def add_atom(self, symbol: str, position: np.ndarray) -> int:
        position = atom_position(symbol, position)
        matches = [index for index, element in enumerate(self.symbols) if element == symbol]
        index = matches[-1] + 1 if matches else len(self.symbols)
        label = self.labels[matches[-1]] if matches else symbol
        self.symbols = self.symbols[:index] + (symbol,) + self.symbols[index:]
        self.labels = self.labels[:index] + (label,) + self.labels[index:]
        self.origins = self.origins[:index] + (None,) + self.origins[index:]
        self.positions = np.insert(self.positions, index, position, axis=0)
        if self.free is not None:
            self.free = np.insert(self.free, index, [True, True, True], axis=0)
        return index

    def add_group(self, symbols: tuple[str, ...], positions: np.ndarray) -> list[int]:
        positions = np.asarray(positions, dtype=float)
        if positions.shape != (len(symbols), 3) or not symbols or not np.isfinite(positions).all():
            raise ValueError("复制组的元素与坐标数量必须一致且非空。")
        for symbol, position in zip(symbols, positions):
            atom_position(symbol, position)
        count = len(self.symbols)
        last = {symbol: index for index, symbol in enumerate(self.symbols)}
        groups = {}
        for index, symbol in enumerate(symbols, count):
            groups.setdefault(symbol, []).append(index)
        order = []
        for index, symbol in enumerate(self.symbols):
            order.append(index)
            if index == last[symbol] and symbol in groups:
                order.extend(groups.pop(symbol))
        for group in groups.values():
            order.extend(group)
        labels = self.labels + tuple(self.labels[last[symbol]] if symbol in last else symbol for symbol in symbols)
        all_symbols = self.symbols + symbols
        origins = self.origins + (None,) * len(symbols)
        self.symbols = tuple(all_symbols[index] for index in order)
        self.labels = tuple(labels[index] for index in order)
        self.origins = tuple(origins[index] for index in order)
        self.positions = np.vstack((self.positions, positions))[order].copy()
        if self.free is not None:
            self.free = np.vstack((self.free, np.ones((len(symbols), 3), dtype=bool)))[order].copy()
        return np.flatnonzero(np.asarray(order) >= count).tolist()

    def set_atom(self, index: int, symbol: str, position: np.ndarray) -> int:
        validate_indices([index], len(self.symbols))
        position = atom_position(symbol, position)
        if symbol == self.symbols[index]:
            self.positions[index] = position
            return index
        flags = None if self.free is None else self.free[index].copy()
        self._remove([index])
        new_index = self.add_atom(symbol, position)
        if flags is not None:
            self.free[new_index] = flags
        return new_index

    def remove_atoms(self, indices: list[int], *, allow_empty=False) -> None:
        validate_indices(indices, len(self.symbols))
        if len(indices) == len(self.symbols) and not allow_empty:
            raise ValueError("结构至少需要保留一个原子。")
        self._remove(indices)

    def _remove(self, indices: list[int]) -> None:
        keep = np.ones(len(self.symbols), dtype=bool)
        keep[indices] = False
        self.symbols = tuple(value for value, retained in zip(self.symbols, keep) if retained)
        self.labels = tuple(value for value, retained in zip(self.labels, keep) if retained)
        self.origins = tuple(value for value, retained in zip(self.origins, keep) if retained)
        self.positions = self.positions[keep].copy()
        if self.free is not None:
            self.free = self.free[keep].copy()


def atom_position(symbol: str, position: np.ndarray) -> np.ndarray:
    if symbol not in atomic_numbers or symbol == "X":
        raise ValueError("请选择有效元素。")
    position = np.asarray(position, dtype=float)
    if position.shape != (3,) or not np.isfinite(position).all():
        raise ValueError("原子需要三个有限的 Cartesian 坐标，单位 Å。")
    return position


def validate_indices(indices: list[int], count: int) -> None:
    if not indices or len(set(indices)) != len(indices):
        raise ValueError("请选择原子；原子编号不能重复。")
    if min(indices) < 0 or max(indices) >= count:
        raise ValueError("原子编号超出范围。")
