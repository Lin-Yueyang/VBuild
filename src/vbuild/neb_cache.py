"""Owned per-page NEB binary cache; copies out of read-only mappings before cleanup."""

from dataclasses import dataclass
from pathlib import Path
import uuid

import numpy as np

from .poscar import PoscarDocument


@dataclass
class CachedFrames:
    values: object
    seed: object
    records: list
    has_forces: list
    title: str

    def frame(self, index):
        from .neb import document
        structure = self.seed.structure
        data = self.values[index]
        forces = data[:, 3:].copy() if self.has_forces[index] else None
        return document(structure.symbols, structure.cell, data[:, :3].copy(), structure.free,
                        title=self.title, observations=self.records[index], forces=forces)


class NebCache:
    def __init__(self, root):
        self.path = Path(root) / ("neb_"+uuid.uuid4().hex[:12])
        self.path.mkdir(parents=True)
        self.files, self.readers = [], []
        self.closed = False

    @property
    def bytes(self):
        return sum(path.stat().st_size for path in self.files)

    def add(self, image, stop):
        from .neb import compatible
        if image.reader is None:
            return
        path = self.path / (image.name+".bin")
        self.files.append(path)
        count, atoms, has_forces = image.count, len(image.seed.structure.symbols), []
        with path.open("wb") as stream:
            for index in range(count):
                if stop():
                    return
                frame = image.frame(index)
                if not compatible(image.seed.structure, frame.structure):
                    raise ValueError("NEB 缓存中发现变化的晶胞或原子顺序。")
                data = np.empty((atoms, 6), dtype=np.float64)
                data[:, :3] = frame.structure.positions
                force = getattr(frame, "forces", None)
                data[:, 3:] = np.nan if force is None else force
                has_forces.append(force is not None)
                data.tofile(stream)
        mapped = np.memmap(path, mode="r", dtype=np.float64, shape=(count, atoms, 6))
        title = image.seed.lines[0].strip() if isinstance(image.seed, PoscarDocument) else image.seed.title
        reader = CachedFrames(mapped, image.seed, image.records, has_forces, title)
        self.readers.append(reader)
        image.reader, image.cache_index, image.cached = reader, -1, None

    def close(self):
        if self.closed:
            return
        for reader in self.readers:
            reader.values._mmap.close()
            reader.values = None
        for path in self.files:
            path.unlink(missing_ok=True)
        self.path.rmdir()
        self.closed = True
