"""Byte-offset trajectory index; only the current frame's coordinates are cached."""

from array import array
from dataclasses import dataclass
from pathlib import Path
import re
import time

import numpy as np
from ase.data import atomic_numbers

from .model import Structure
from .poscar import PoscarDocument
from .source import identity, open_source


def is_configuration(line):
    return re.fullmatch(rb'\s*(?:direct|cartesian)\s+configuration\s*=\s*\d+\s*', line, re.IGNORECASE) is not None


def coordinate_lines(stream, count, stop=lambda: False):
    for index in range(count):
        line = stream.readline()
        if not line.strip():
            raise ValueError(f"轨迹帧坐标不足：需要 {count} 个原子，读取到 {index} 个。")
        if index % 1024 == 0 and stop():
            return
        yield line


def xdat_header(stream):
    header = [stream.readline() for _index in range(7)]
    counts = [int(value) for value in header[6].split()]
    symbols = header[5].decode("utf-8").split()
    if not symbols or not counts or len(symbols) != len(counts) or min(counts) <= 0:
        raise ValueError("XDATCAR 需要有效的 VASP 5/6 元素与数量头部。")
    return b"".join(header), sum(counts)


@dataclass
class XyzDocument:
    structure: Structure
    comment: str
    source: bytes
    restart: bool = False
    dirty: bool = False

    @classmethod
    def read_frame(cls, stream):
        count_line, comment_line = stream.readline(), stream.readline()
        count = int(count_line)
        comment = comment_line.decode("utf-8").rstrip("\r\n")
        if count <= 0:
            raise ValueError("XYZ 每帧需要至少一个原子。")
        lines = list(coordinate_lines(stream, count))
        rows = [line.decode("utf-8").split() for line in lines]
        symbols = tuple(row[0] for row in rows)
        if any(symbol not in atomic_numbers for symbol in symbols) or any(len(row) < 4 for row in rows):
            raise ValueError("XYZ 坐标行需要元素符号和三个笛卡尔坐标。")
        positions = np.array([[float(value) for value in row[1:4]] for row in rows])
        if not np.isfinite(positions).all():
            raise ValueError("XYZ 坐标必须是有限数值。")
        structure = Structure(symbols, None, positions, None, None, symbols, tuple(range(count)))
        return cls(structure, comment, count_line + comment_line + b"".join(lines))

    def to_bytes(self, **_options):
        return self.source


@dataclass
class Trajectory:
    path: Path
    kind: str
    stamp: tuple
    offsets: array
    headers: array

    @classmethod
    def begin(cls, path, kind):
        path = Path(path).resolve()
        stamp = identity(path)
        with open_source(path) as stream:
            if kind == "xyz":
                offsets, headers = array("Q", [0]), array("Q")
            else:
                _header, _count = xdat_header(stream)
                offset = stream.tell()
                marker = stream.readline()
                while marker and not marker.strip():
                    offset = stream.tell()
                    marker = stream.readline()
                if not is_configuration(marker):
                    raise ValueError("XDATCAR 缺少第一帧 configuration 标记。")
                offsets, headers = array("Q", [offset]), array("Q", [0])
        reader = cls(path, kind, stamp, offsets, headers)
        return reader, reader.frame(0)

    def frame(self, index):
        if identity(self.path) != self.stamp:
            raise ValueError("轨迹源文件已改变，请重新导入；当前帧仍保留。")
        with open_source(self.path) as stream:
            if self.kind == "xyz":
                stream.seek(self.offsets[index])
                return XyzDocument.read_frame(stream)
            stream.seek(self.headers[index])
            header, count = xdat_header(stream)
            stream.seek(self.offsets[index])
            mode = stream.readline().split()[0].lower()
            if mode not in (b"direct", b"cartesian"):
                raise ValueError("XDATCAR 帧需要 Direct/Cartesian configuration 标记。")
            rows = b"".join(coordinate_lines(stream, count))
            return PoscarDocument.from_bytes(header + mode.capitalize() + b"\n" + rows)

    def index(self, progress=lambda _count: None, stop=lambda: False):
        if identity(self.path) != self.stamp:
            raise ValueError("轨迹源文件已改变，请重新导入。")
        offsets, headers = array("Q"), array("Q")
        emitted = time.monotonic()
        with open_source(self.path) as stream:
            header_offset, count = 0, 0
            if self.kind == "xdatcar":
                _header, count = xdat_header(stream)
            while not stop():
                offset = stream.tell()
                line = stream.readline()
                if not line:
                    break
                if not line.strip():
                    continue
                if self.kind == "xyz":
                    count = int(line)
                    if count <= 0 or not stream.readline():
                        raise ValueError("XYZ 帧的原子数量或注释行无效。")
                elif not is_configuration(line):
                    header_offset = offset
                    stream.seek(offset)
                    _header, count = xdat_header(stream)
                    continue
                for _row in coordinate_lines(stream, count, stop):
                    pass
                if stop():
                    return None
                offsets.append(offset)
                if self.kind == "xdatcar":
                    headers.append(header_offset)
                if time.monotonic() - emitted > 0.2:
                    progress(len(offsets))
                    emitted = time.monotonic()
        if stop():
            return None
        return Trajectory(self.path, self.kind, self.stamp, offsets, headers)
