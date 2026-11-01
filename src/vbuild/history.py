"""Undo model edits, including atom count, constraints and temperature."""

from dataclasses import dataclass
from collections.abc import Callable

import numpy as np

from .poscar import PoscarDocument, equal_optional


@dataclass
class State:
    cell: np.ndarray | None
    positions: np.ndarray
    free: np.ndarray | None
    symbols: tuple[str, ...]
    labels: tuple[str, ...]
    origins: tuple[int | None, ...]
    temperature: float | None
    temperature_pending: bool
    periodic: tuple | None

    @classmethod
    def capture(cls, document: PoscarDocument) -> "State":
        structure = document.structure
        return cls(None if structure.cell is None else structure.cell.copy(), structure.positions.copy(),
                   None if structure.free is None else structure.free.copy(),
                   structure.symbols, structure.labels, structure.origins,
                   structure.temperature, structure.temperature_pending, structure.periodic)

    def restore(self, document: PoscarDocument) -> None:
        structure = document.structure
        structure.cell = None if self.cell is None else self.cell.copy()
        structure.positions = self.positions.copy()
        structure.free = None if self.free is None else self.free.copy()
        structure.symbols, structure.labels, structure.origins = self.symbols, self.labels, self.origins
        structure.temperature, structure.temperature_pending = self.temperature, self.temperature_pending
        structure.periodic = self.periodic


@dataclass
class Edit:
    before: State
    after: State
    selection_before: list[int]
    selection_after: list[int]
    label: str


class History:
    def __init__(self, limit: int = 100):
        self.limit = limit
        self.entries: list[Edit] = []
        self.cursor = 0

    def apply(self, document: PoscarDocument, operation: Callable[[], object], *,
              selection: list[int] | tuple[int, ...] = (), selection_after: Callable | None = None,
              label: str = "编辑结构") -> object:
        document.ensure_editable()
        before = State.capture(document)
        result = operation()
        after = State.capture(document)
        if (np.array_equal(before.cell, after.cell) and np.array_equal(before.positions, after.positions)
                and equal_optional(before.free, after.free)
                and before.symbols == after.symbols and before.labels == after.labels
                and before.origins == after.origins and before.temperature == after.temperature
                and before.temperature_pending == after.temperature_pending and before.periodic == after.periodic):
            return result
        selected_after = list(selection) if selection_after is None else selection_after(result)
        edit = Edit(before, after, list(selection), selected_after, label)
        self.entries = self.entries[:self.cursor] + [edit]
        self.entries = self.entries[-self.limit:]
        self.cursor = len(self.entries)
        return result

    def seek(self, document: PoscarDocument, target: int) -> list[int]:
        if not 0 <= target <= len(self.entries):
            raise ValueError("目标历史步骤超出范围。")
        if target == self.cursor:
            return []
        if target < self.cursor:
            edit = self.entries[target]
            state, selection = edit.before, edit.selection_before
        else:
            edit = self.entries[target - 1]
            state, selection = edit.after, edit.selection_after
        state.restore(document)
        self.cursor = target
        return selection.copy()

    def undo(self, document: PoscarDocument) -> list[int]:
        return self.seek(document, self.cursor - 1) if self.cursor else []

    def redo(self, document: PoscarDocument) -> list[int]:
        return self.seek(document, self.cursor + 1) if self.cursor < len(self.entries) else []
