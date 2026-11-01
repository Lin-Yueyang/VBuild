"""Indexed XML/extxyz/dump frames; cache only current coordinates and scalar records."""

from array import array
from dataclasses import dataclass, field
from io import BytesIO, StringIO
import re
import time
from xml.etree import ElementTree as ET

import numpy as np
from ase.data import atomic_numbers

from .formats import FrameDocument, from_atoms
from .model import Structure
from .source import identity, open_source
from .trajectory import Trajectory, coordinate_lines


class IncompleteXml(ValueError):
    pass


def xml_spans(stream, stop=lambda: False):
    carry, offset, last, start = b"", 0, -1, None
    pattern = re.compile(rb"</?calculation\b[^>]*>")
    while not stop() and (chunk := stream.read(1024 * 1024)):
        data = carry + chunk
        base = offset - len(carry)
        for match in pattern.finditer(data):
            end = base + match.end()
            if end <= last:
                continue
            last = end
            if match[0].startswith(b"</"):
                if start is None:
                    raise ValueError("XML calculation 结束标记没有起点。")
                yield start, end
                start = None
            else:
                if start is not None:
                    raise ValueError("XML calculation 标记嵌套错误。")
                start = base + match.start()
        offset += len(chunk)
        carry = data[-1024:]
    if start is not None and not stop():
        raise IncompleteXml("XML 最后一个离子步尚未写完，只展示已完成帧。")


class SliceStream:
    def __init__(self, stream, count):
        self.stream, self.left = stream, count

    def read(self, size=-1):
        size = self.left if size < 0 else min(self.left, size)
        value = self.stream.read(size)
        self.left -= len(value)
        return value


def xml_header(content):
    root = ET.fromstring(content + b"</modeling>")
    rows = root.findall("./atominfo/array[@name='atoms']/set/rc")
    symbols = tuple(row.find("c").text.strip() for row in rows)
    if not symbols or any(symbol not in atomic_numbers or symbol == "X" for symbol in symbols):
        raise ValueError("vasprun.xml 缺少有效的真实元素表。")
    selective = root.find("./structure[@name='initialpos']/varray[@name='selective']")
    free = None
    if selective is not None:
        flags = [node.text.split() for node in selective]
        if len(flags) != len(symbols) or any(len(row) != 3 or any(x not in ('T', 'F') for x in row) for row in flags):
            raise ValueError("XML 固定约束与原子数不符。")
        free = np.array(flags) == "T"
    return symbols, free


def xml_frame(stream, length, symbols, free):
    arrays, observations, path, nodes = {}, {}, [], []
    active = None
    for event, node in ET.iterparse(SliceStream(stream, length), events=("start", "end")):
        if event == "start":
            path.append(node.tag)
            nodes.append(node)
            if node.tag == "varray" and node.get("name") in ("basis", "positions", "forces", "stress"):
                active = node.get("name")
                arrays[active] = []
            continue
        if node.tag == "v" and active is not None:
            arrays[active].append([float(x.replace("D", "E")) for x in node.text.split()])
        if path == ["calculation", "energy", "i"]:
            observations[node.get("name")] = float(node.text.replace("D", "E"))
        if node.tag == "varray":
            active = None
        node.clear()
        nodes.pop()
        if nodes:
            nodes[-1].remove(node)
        path.pop()
    cell = np.asarray(arrays["basis"], dtype=float)
    fraction = np.asarray(arrays["positions"], dtype=float)
    if cell.shape != (3, 3) or fraction.shape != (len(symbols), 3) or not np.isfinite(cell).all() or abs(np.linalg.det(cell)) < 1e-10:
        raise ValueError("XML 晶胞或原子坐标无效。")
    structure = Structure(symbols, cell, fraction @ cell, None if free is None else free.copy(), None,
                          symbols, tuple(range(len(symbols))))
    if not np.isfinite(structure.positions).all() or not all(np.isfinite(x) for x in observations.values()):
        raise ValueError("XML 坐标/能量含无效数值。")
    forces = None if "forces" not in arrays else np.asarray(arrays["forces"], dtype=float)
    if forces is not None:
        if forces.shape != structure.positions.shape or not np.isfinite(forces).all():
            raise ValueError("XML 原子力与坐标数量不符。")
        observations["max_force"] = float(np.linalg.norm(forces, axis=1).max())
    return FrameDocument(structure, "vasprun.xml", unsaved=False, observations=observations, forces=forces)


def ext_frame(stream):
    from ase.io import read
    count_line, comment = stream.readline(), stream.readline()
    count = int(count_line)
    if count <= 0:
        raise ValueError("extxyz 原子数量必须为正。")
    content = count_line + comment + b"".join(coordinate_lines(stream, count))
    return from_atoms(read(StringIO(content.decode("utf-8")), format="extxyz"), "extxyz")


@dataclass
class ReportTrajectory(Trajectory):
    records: list[dict] = field(default_factory=list)
    symbols: tuple = ()
    free: object = None
    mapping: dict = field(default_factory=dict)
    units: str = "metal"
    notice: str = ""

    @classmethod
    def begin(cls, path, kind, mapping=None, units="metal"):
        reader = cls(path, kind, identity(path), array("Q", [0]), array("Q"),
                     mapping={} if mapping is None else mapping, units=units)
        if kind == "xml":
            with open_source(path) as stream:
                start, end = next(xml_spans(stream), (None, None))
                if start is None:
                    raise ValueError("vasprun.xml 没有已完成的离子步。")
                stream.seek(0)
                reader.symbols, reader.free = xml_header(stream.read(start))
                reader.offsets, reader.headers = array("Q", [start]), array("Q", [end])
        document = reader.frame(0)
        reader.records = [document.observations]
        return reader, document

    def read_document(self, stream, index=None):
        if self.kind == "xml":
            return xml_frame(stream, self.headers[index] - self.offsets[index], self.symbols, self.free)
        if self.kind == "extxyz":
            return ext_frame(stream)
        from .lammps import dump_frame
        return dump_frame(stream, self.mapping, self.units)

    def frame(self, index):
        if identity(self.path) != self.stamp:
            raise ValueError("轨迹源文件已改变，请重新导入；当前帧仍保留。")
        with open_source(self.path) as stream:
            stream.seek(self.offsets[index])
            return self.read_document(stream, index)

    def index(self, progress=lambda _count: None, stop=lambda: False):
        if identity(self.path) != self.stamp:
            raise ValueError("轨迹源文件已改变，请重新导入。")
        offsets, ends, records = array("Q"), array("Q"), []
        emitted = time.monotonic()
        with open_source(self.path) as stream:
            if self.kind == "xml":
                try:
                    for start, end in xml_spans(stream, stop):
                        offsets.append(start)
                        ends.append(end)
                except IncompleteXml as error:
                    self.notice = str(error)
                self.offsets, self.headers = offsets, ends
            while not stop():
                if self.kind == "xml":
                    index = len(records)
                    if index == len(offsets):
                        break
                    stream.seek(offsets[index])
                    document = self.read_document(stream, index)
                else:
                    offset = stream.tell()
                    line = stream.readline()
                    if not line:
                        break
                    if not line.strip():
                        continue
                    stream.seek(offset)
                    document = self.read_document(stream)
                    offsets.append(offset)
                records.append(document.observations)
                if time.monotonic() - emitted > .2:
                    progress(len(records))
                    emitted = time.monotonic()
        if stop():
            return None
        if not records or identity(self.path) != self.stamp:
            raise ValueError("源文件改变或没有有效轨迹帧。")
        self.offsets, self.headers, self.records = offsets, ends, records
        return self
