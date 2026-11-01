"""Two-stage NEB import: geometry probes, then indexed complete ionic steps."""

from array import array
from copy import deepcopy
from dataclasses import dataclass, field
from functools import cached_property
import gzip
from io import BytesIO
from pathlib import Path
import os
import re
from xml.etree import ElementTree as ET

import numpy as np
from ase.data import atomic_numbers
from ase.geometry import find_mic

from .formats import FrameDocument
from .model import Structure
from .poscar import PoscarDocument
from .reports import IncompleteXml, ReportTrajectory, xml_spans, xml_header, xml_frame
from .source import identity, open_source
from .trajectory import Trajectory, xdat_header

NAMES = ("vasprun.xml", "OUTCAR", "XDATCAR", "CONTCAR", "POSCAR")
ENERGIES = (("e_0_energy", "E₀ / sigma→0"), ("e_wo_entrp", "无熵能量"), ("e_fr_energy", "自由能 TOTEN"))
NUMBER = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[EeDd][-+]?\d+)?"


def values(line):
    return [float(word.replace("D", "E").replace("d", "e")) for word in line.split()]


def complete_matrix(stream, count, columns):
    lines = [stream.readline() for _index in range(count)]
    rows = [line.split() for line in lines]
    if any(len(row) < columns for row in rows):
        return None
    try:
        return np.array([values(b" ".join(row[:columns]).decode()) for row in rows])
    except ValueError:
        if not lines[-1].endswith((b"\n", b"\r")) and not stream.peek(1):
            return None
        raise


def compatible(first, second):
    return (first.symbols == second.symbols and first.pbc == second.pbc and
            (first.cell is None and second.cell is None or first.cell is not None and second.cell is not None
             and np.allclose(first.cell, second.cell, rtol=1e-5, atol=1e-5)))


def same_geometry(first, second):
    if not compatible(first, second):
        return False
    delta = second.positions-first.positions
    offsets = np.rint(np.linalg.solve(first.cell.T, delta.T).T) * first.pbc
    return np.max(np.abs(delta-offsets @ first.cell)) < 1e-5


def document(symbols, cell, positions, free=None, *, title="NEB", observations=None, forces=None):
    positions = np.asarray(positions, dtype=float)
    cell = np.asarray(cell, dtype=float)
    if not symbols or positions.shape != (len(symbols), 3) or cell.shape != (3, 3):
        raise ValueError("NEB 元素、晶胞与原子坐标数量不一致。")
    if not np.isfinite(positions).all() or not np.isfinite(cell).all() or abs(np.linalg.det(cell)) < 1e-10:
        raise ValueError("NEB 晶胞或坐标无效。")
    if free is not None and free.shape != positions.shape:
        raise ValueError("NEB 固定约束与原子数量不一致。")
    structure = Structure(symbols, cell.copy(), positions.copy(), None if free is None else free.copy(),
                          None, symbols, tuple(range(len(symbols))))
    return FrameDocument(structure, title, unsaved=False, observations={} if observations is None else observations,
                         forces=forces)


def xml_probe(path):
    symbols = ()
    with open_source(path) as stream:
        for event, node in ET.iterparse(stream, events=("start", "end")):
            if event == "start" and node.tag == "calculation":
                break
            if event != "end":
                continue
            if node.tag == "atominfo":
                symbols = tuple(row.find("c").text.strip() for row in node.findall("./array[@name='atoms']/set/rc"))
                node.clear()
            elif node.tag == "structure" and node.get("name") == "initialpos":
                if any(symbol not in atomic_numbers or symbol == "X" for symbol in symbols):
                    raise ValueError("XML 元素表无效。")
                cell = np.array([values(row.text) for row in node.findall("./crystal/varray[@name='basis']/v")])
                fraction = np.array([values(row.text) for row in node.findall("./varray[@name='positions']/v")])
                selective = node.find("./varray[@name='selective']")
                flags = [] if selective is None else [row.text.split() for row in selective]
                if selective is not None and (len(flags) != len(symbols) or any(len(row) != 3 or any(flag not in ("T", "F") for flag in row) for row in flags)):
                    raise ValueError("XML 固定约束与原子数量不符。")
                free = None if selective is None else np.array(flags) == "T"
                if cell.shape != (3, 3) or fraction.shape != (len(symbols), 3):
                    raise ValueError("XML 初始晶胞或坐标数量无效。")
                return document(symbols, cell, fraction @ cell, free, title=path.name)
            elif node.tag in ("parameters", "incar", "kpoints", "generator"):
                node.clear()
    raise ValueError("XML 尚无完整的 initialpos 结构。")


def outcar_source(path):
    return gzip.open(path, "rb") if Path(path).suffix.lower() == ".gz" else open_source(path)


def outcar_probe(path):
    species, titles, counts, cell = [], [], [], None
    with outcar_source(path) as stream:
        while line := stream.readline():
            text = line.decode("utf-8", errors="replace")
            if "VRHFIN" in text:
                species.append(text.split("=")[1].split(":")[0].strip())
            elif "TITEL" in text:
                parts = text.split("=")[1].split()
                titles.append(parts[1].split("_")[0])
            elif "ions per type" in text:
                counts = [int(value) for value in text.split("=")[1].split()]
            elif "direct lattice vectors" in text:
                cell = np.array([values(stream.readline().decode())[:3] for _index in range(3)])
            initial = "position of ions in" in text
            force_table = "POSITION" in text and "TOTAL-FORCE" in text
            if (initial or force_table) and counts and cell is not None:
                names = (species or titles)[:len(counts)]
                if len(names) != len(counts) or any(name not in atomic_numbers or name == "X" for name in names):
                    raise ValueError("OUTCAR 缺少明确元素名称。")
                symbols = tuple(symbol for symbol, count in zip(names, counts) for _index in range(count))
                if force_table:
                    stream.readline()
                rows = [stream.readline().decode().split() for _index in range(sum(counts))]
                if any(len(row) < 3 for row in rows):
                    raise ValueError("OUTCAR 初始结构尚未写完。")
                points = np.array([values(" ".join(row[:3])) for row in rows])
                if "direct" in text.lower() and initial:
                    points = points @ cell
                return document(symbols, cell, points, title=path.name)
    raise ValueError("OUTCAR 尚无完整的晶胞、元素与原子位置。")


def probe(path):
    if path.name == "vasprun.xml":
        return xml_probe(path)
    if path.name == "OUTCAR":
        return outcar_probe(path)
    if path.name == "XDATCAR":
        return Trajectory.begin(path, "xdatcar")[1]
    return PoscarDocument.read(path)


@dataclass
class ImageScan:
    folder: Path
    files: dict
    seeds: dict = field(default_factory=dict)
    errors: dict = field(default_factory=dict)
    source: str = ""
    included: bool = True
    endpoint: bool = False

    @property
    def seed(self):
        return self.seeds.get(self.source)


@dataclass
class GroupScan:
    root: Path
    images: list
    config: dict

    def problems(self):
        chosen = [item for item in self.images if item.included]
        if not chosen:
            return ["请选择至少一个包含完整结构的图像。"]
        reference = chosen[0].seed
        issues = []
        for item in chosen:
            if item.seed is None:
                issues.append(item.folder.name + "：没有完整结构")
            elif reference is not None and not compatible(reference.structure, item.seed.structure):
                issues.append(item.folder.name + "：元素顺序、原子数量或晶胞不一致")
        return issues


def scan(root, progress=lambda _text: None, stop=lambda: False):
    root = Path(root).resolve()
    groups = {}
    for parent, directories, _files in os.walk(root, followlinks=False):
        if stop():
            return []
        directories[:] = sorted(name for name in directories if not name.startswith("."))
        numbered = [name for name in directories if re.fullmatch(r"\d{2,}", name)]
        if not numbered:
            continue
        images = []
        for name in sorted(numbered, key=lambda value: (int(value), value)):
            folder = Path(parent) / name
            found = {name: folder / name for name in NAMES if (folder / name).is_file()}
            item = ImageScan(folder, found)
            for name, path in found.items():
                if stop():
                    return []
                progress(str(path.relative_to(root)))
                # A bad/incomplete candidate is visible; it never substitutes another file silently.
                try:
                    item.seeds[name] = probe(path)
                except (OSError, ValueError, KeyError, IndexError, ET.ParseError) as error:
                    item.errors[name] = str(error)
            constraint_source = item.seeds.get("POSCAR", item.seeds.get("CONTCAR"))
            if constraint_source is not None:
                for seed in item.seeds.values():
                    if seed.structure.free is None and compatible(constraint_source.structure, seed.structure):
                        seed.structure.free = (None if constraint_source.structure.free is None else constraint_source.structure.free.copy())
            item.source = next((name for name in NAMES if name in item.seeds), "")
            item.included = bool(item.source)
            images.append(item)
        config = {}
        for name in ("INCAR", "KPOINTS"):
            path = Path(parent) / name
            if path.is_file():
                with open_source(path) as stream:
                    config[name] = stream.read(65536).decode("utf-8-sig", errors="replace")
        incar = re.sub(r"[#!].*", "", config.get("INCAR", ""))
        count = re.search(r"\bIMAGES\s*=\s*(\d+)", incar, re.IGNORECASE)
        last = int(count[1])+1 if count else max(int(item.folder.name) for item in images) if any(int(item.folder.name) == 0 for item in images) else None
        for item in images:
            item.endpoint = int(item.folder.name) in (0, last)
        groups[parent] = GroupScan(Path(parent), images, config)
    return list(groups.values())


@dataclass
class OutcarReader:
    path: Path
    stamp: tuple
    seed: object
    offsets: list = field(default_factory=list)
    cells: list = field(default_factory=list)
    records: list = field(default_factory=list)
    notice: str = ""
    fixed_cell: bool = True

    def index(self, stop=lambda: False):
        cell = self.seed.structure.cell.copy()
        pending, current, ionic = {}, None, None
        count = len(self.seed.structure.symbols)
        with outcar_source(self.path) as stream:
            while not stop() and (line := stream.readline()):
                if not (b"Iteration" in line or b"direct lattice vectors" in line or b"POSITION" in line or
                        b"FORCES:" in line or b"TOTEN" in line or b"entropy" in line or b"vasp." in line):
                    continue
                text = line.decode("utf-8", errors="replace")
                if text.lstrip().startswith("vasp."):
                    pending, current, ionic = {}, None, None
                iteration = re.search(r"Iteration\s+(\d+)\s*\(", text)
                if iteration and iteration[1] != ionic:
                    ionic, current, pending = iteration[1], None, {}
                if "direct lattice vectors" in text:
                    matrix = complete_matrix(stream, 3, 3)
                    if matrix is None:
                        self.notice = "末尾晶胞尚未写完，保留完整步骤。"
                        break
                    cell = matrix
                if "POSITION" in text and "TOTAL-FORCE" in text:
                    stream.readline()
                    offset = stream.tell()
                    matrix = complete_matrix(stream, count, 6)
                    if matrix is None:
                        self.notice = "末尾 POSITION / TOTAL-FORCE 尚未写完，保留完整步骤。"
                        break
                    if not np.isfinite(matrix).all():
                        raise ValueError("OUTCAR 位置或力包含非有限数值。")
                    if self.fixed_cell and not np.allclose(cell, self.seed.structure.cell, rtol=1e-5, atol=1e-5):
                        raise ValueError("OUTCAR 晶胞发生变化，不是固定晶胞 NEB。")
                    current = pending.copy()
                    current["raw_max_force"] = float(np.linalg.norm(matrix[:, 3:], axis=1).max())
                    self.offsets.append(offset)
                    self.cells.append(cell.copy())
                    self.records.append(current)
                    pending = {}
                summary = re.search(r"FORCES:\s*max atom,\s*RMS\s+(" + NUMBER + r")\s+(" + NUMBER + r")", text)
                if summary:
                    maximum, rms = values(" ".join(summary.groups()))
                    if not np.isfinite([maximum, rms]).all():
                        raise ValueError("OUTCAR 优化力包含非有限数值。")
                    (pending if current is None else current).update(max_force=maximum, force_rms=rms)
                energy = {}
                match = re.search(r"free\s+energy\s+TOTEN\s*=\s*(" + NUMBER + r")", text)
                if match:
                    energy["e_fr_energy"] = values(match[1])[0]
                match = re.search(r"energy\s+without\s+entropy\s*=\s*(" + NUMBER + r").*?energy\(sigma->0\)\s*=\s*(" + NUMBER + r")", text)
                if match:
                    energy.update(e_wo_entrp=values(match[1])[0], e_0_energy=values(match[2])[0])
                if energy:
                    if not all(np.isfinite(value) for value in energy.values()):
                        raise ValueError("OUTCAR 能量包含非有限数值。")
                    (pending if current is None else current).update(energy)
        return self

    def frame(self, index):
        if identity(self.path) != self.stamp:
            raise ValueError("NEB 源文件改变，请用刷新重新导入；当前显示仍保留。")
        with outcar_source(self.path) as stream:
            stream.seek(self.offsets[index])
            matrix = np.array([values(stream.readline().decode())[:6] for _index in self.seed.structure.symbols])
        return document(self.seed.structure.symbols, self.cells[index], matrix[:, :3], self.seed.structure.free,
                        title=self.path.name, observations=self.records[index], forces=matrix[:, 3:])


def xdat_index(path, stop=lambda: False):
    reader, _first = Trajectory.begin(path, "xdatcar")
    offsets, headers = array("Q"), array("Q")
    notice = ""
    with open_source(path) as stream:
        _header, count = xdat_header(stream)
        header_offset = 0
        while not stop():
            offset = stream.tell()
            line = stream.readline()
            if not line:
                break
            if not line.strip():
                continue
            if not line.lower().startswith((b"direct configuration", b"cartesian configuration")):
                header_offset = offset
                stream.seek(offset)
                _header, count = xdat_header(stream)
                continue
            coordinates = complete_matrix(stream, count, 3)
            if coordinates is None:
                notice = "XDATCAR 最后一帧尚未写完，保留完整帧。"
                break
            if not np.isfinite(coordinates).all():
                raise ValueError("XDATCAR 坐标含非有限值。")
            offsets.append(offset)
            headers.append(header_offset)
    reader.offsets, reader.headers = offsets, headers
    return reader, notice


@dataclass
class NebXmlReader(ReportTrajectory):
    parts: list = field(default_factory=list)

    def frame(self, index):
        if identity(self.path) != self.stamp:
            raise ValueError("NEB 源文件改变，请用刷新重新导入；当前显示仍保留。")
        content = [b"<calculation>"]
        with open_source(self.path) as stream:
            for start, end in self.parts[index]:
                stream.seek(start)
                content.append(stream.read(end-start))
        content.append(b"</calculation>")
        body = b"".join(content)
        result = xml_frame(BytesIO(body), len(body), self.symbols, self.free)
        result.observations = self.records[index]
        return result


def xml_index(path, seed, stop=lambda: False):
    offsets, ends, records, notice = array("Q"), array("Q"), [], ""
    parts = []
    structure_pattern = re.compile(rb"<structure\b[^>]*>.*?</structure>", re.DOTALL)
    force_pattern = re.compile(rb"<varray\b(?=[^>]*\bname\s*=\s*['\"]forces['\"])[^>]*>.*?</varray>", re.DOTALL)
    with open_source(path) as stream:
        try:
            for start, end in xml_spans(stream, stop):
                offsets.append(start)
                ends.append(end)
        except IncompleteXml as error:
            notice = str(error)
        if not offsets:
            return None, "XML 没有完整计算步骤，保留 initialpos。"
        stream.seek(0)
        symbols, free = xml_header(stream.read(offsets[0]))
        for start, end in zip(offsets, ends):
            if stop():
                break
            stream.seek(start)
            body = stream.read(end-start)
            record = xml_frame(BytesIO(body), len(body), symbols, free)
            if not compatible(seed.structure, record.structure):
                raise ValueError("XML 中的元素或晶胞发生变化，不是固定晶胞 NEB。")
            if "max_force" in record.observations:
                record.observations["raw_max_force"] = record.observations.pop("max_force")
            records.append(record.observations)
            structure = list(structure_pattern.finditer(body))[-1]
            forces = list(force_pattern.finditer(body))
            parts.append([(start+structure.start(), start+structure.end())] +
                         ([(start+forces[-1].start(), start+forces[-1].end())] if forces else []))
    reader = NebXmlReader(path, "xml", identity(path), offsets, ends, records=records,
                         symbols=symbols, free=free, notice=notice, parts=parts)
    return reader, notice


@dataclass
class NebImage:
    name: str
    path: Path
    seed: object
    reader: object = None
    records: list = field(default_factory=lambda: [{}])
    notice: str = ""
    cache_index: int = -1
    cached: object = None
    source_step: int = 1
    endpoint: bool = False

    @property
    def count(self):
        return len(self.records)

    def frame(self, index):
        index = min(index, self.count - 1)
        if index == self.cache_index:
            return self.cached
        result = deepcopy(self.seed) if self.reader is None else self.reader.frame(index)
        result.structure.free = None if self.seed.structure.free is None else self.seed.structure.free.copy()
        if isinstance(result, FrameDocument):
            result.observations = self.records[index]
        self.cache_index, self.cached = index, result
        return result


def endpoint_frame(image, endpoint):
    if not endpoint:
        return image
    count = image.count
    image.endpoint = True
    image.seed = deepcopy(image.frame(count-1))
    image.records = [image.records[-1]]
    image.reader, image.cache_index, image.cached = None, -1, None
    image.source_step = count
    image.notice = f"端点仅取最后完整步（来源第 {count} 帧）。" + image.notice
    return image


def load_image(item, stop=lambda: False):
    path, seed = item.files[item.source], item.seed
    reader, notice = None, ""
    if item.source == "OUTCAR":
        reader = OutcarReader(path, identity(path), seed).index(stop)
        notice = reader.notice
    elif item.source == "vasprun.xml":
        reader, notice = xml_index(path, seed, stop)
    elif item.source == "XDATCAR":
        reader, notice = xdat_index(path, stop)
    if reader is not None and not reader.offsets:
        reader = None
        notice += " 没有完整优化步骤，保留扫描到的初始结构。"
    records = ([{} for _offset in reader.offsets] if isinstance(reader, Trajectory) and not isinstance(reader, ReportTrajectory)
               else reader.records if reader is not None else [{}])
    image = NebImage(item.folder.name, path, seed, reader, records, notice)
    # Static geometry can borrow observations only from a geometrically matching output step.
    auxiliary = next((name for name in ("vasprun.xml", "OUTCAR") if name in item.seeds), None)
    if item.source in ("POSCAR", "CONTCAR", "XDATCAR") and auxiliary is not None:
        other_path, other_seed = item.files[auxiliary], item.seeds[auxiliary]
        other = (xml_index(other_path, other_seed, stop)[0] if auxiliary == "vasprun.xml" else
                 OutcarReader(other_path, identity(other_path), other_seed).index(stop))
        if other is None:
            attach_summary(image, item, stop)
            return endpoint_frame(image, item.endpoint)
        candidates = range(len(other.offsets) - 1, -1, -1)
        if image.reader is None:
            for index in candidates:
                if stop():
                    break
                observed = other.frame(index)
                if same_geometry(seed.structure, observed.structure):
                    image.seed = deepcopy(seed)
                    image.seed.observations, image.seed.forces = observed.observations, observed.forces
                    image.records = [observed.observations]
                    break
        elif image.count == len(other.offsets):
            for index in range(image.count):
                if stop():
                    break
                geometric, observed = reader.frame(index), other.frame(index)
                if same_geometry(geometric.structure, observed.structure):
                    image.records[index] = observed.observations
    attach_summary(image, item, stop)
    return endpoint_frame(image, item.endpoint)


def attach_summary(image, item, stop):
    """Associate OUTCAR optimizer forces only with the same evaluated geometry."""
    if item.source == "OUTCAR" or "OUTCAR" not in item.seeds:
        return
    path = item.files["OUTCAR"]
    source = OutcarReader(path, identity(path), item.seeds["OUTCAR"]).index(stop)
    pairs = ([(0, step) for step in reversed(range(len(source.offsets)))] if image.reader is None else
             [(step, step) for step in range(min(image.count, len(source.offsets)))])
    for target, step in pairs:
        if stop():
            return
        record = source.records[step]
        if "max_force" not in record:
            continue
        first, second = image.frame(target).structure, source.frame(step).structure
        if same_geometry(first, second):
            image.records[target].update({key: record[key] for key in ("max_force", "force_rms")})
            if image.reader is None:
                break


@dataclass
class NebRun:
    path: Path
    images: list
    config: dict
    active: int = 0
    selected: set = field(default_factory=set)
    opacity: float = .30
    bond_opacity: float = .18
    opaque: set = field(default_factory=set)
    energy_key: str = "e_0_energy"
    show_data: bool = True
    show_history: bool = False
    show_profile: bool = True
    snapshot_index: int = -1
    snapshot: list = field(default_factory=list)
    kind: str = "neb"
    cache: object = None
    analysis_selected: set = field(default_factory=set)

    @cached_property
    def ediffg(self):
        text = "\n".join(line.split("#", 1)[0].split("!", 1)[0] for line in self.config.get("INCAR", "").splitlines())
        matches = re.findall(r"\bEDIFFG\s*=\s*(" + NUMBER + r")", text, re.IGNORECASE)
        return values(matches[-1])[0] if matches else None

    def convergence(self, image_index, frame):
        image, threshold = self.images[image_index], self.ediffg
        if image.endpoint or threshold is None or threshold == 0:
            return "-"
        actual = min(frame, image.count-1)
        record = image.records[actual]
        if threshold < 0:
            force = record.get("max_force")
            return "-" if force is None else "是" if force < -threshold else "否"
        energy = record.get("e_fr_energy")
        previous = image.records[actual-1].get("e_fr_energy") if actual else None
        return "-" if energy is None or previous is None else "是" if abs(energy-previous) < threshold else "否"

    def close_cache(self):
        if self.cache is not None:
            self.cache.close()
            self.cache = None

    @property
    def offsets(self):
        return range(max(image.count for image in self.images))

    @property
    def export_count(self):
        return self.images[self.active].count

    @property
    def notice(self):
        notes = [image.name + ": " + image.notice for image in self.images if image.notice]
        return "；".join(notes)

    def at(self, index):
        if index == self.snapshot_index:
            return self.snapshot
        prepared = [image.frame(index) for image in self.images]
        reference = prepared[0].structure
        if any(not compatible(reference, value.structure) for value in prepared):
            raise ValueError("当前 NEB 图像的原子顺序/晶胞不一致，停止播放。")
        self.snapshot_index, self.snapshot = index, prepared
        return prepared

    def frame(self, index):
        return self.at(index)[self.active]

    def export_frame(self, index):
        image = self.images[self.active]
        result = deepcopy(image.seed) if image.reader is None else image.reader.frame(index)
        result.structure.free = None if image.seed.structure.free is None else image.seed.structure.free.copy()
        return result

    def energy_reference(self, index):
        energies = [image.records[min(index, image.count - 1)].get(self.energy_key) for image in self.images]
        reference = next((image for image, value in enumerate(energies) if value is not None), None)
        return reference, energies

    def relative_energies(self, index):
        reference_index, energies = self.energy_reference(index)
        reference = None if reference_index is None else energies[reference_index]
        return [None if value is None or reference is None else value - reference for value in energies]

    def distances(self, index):
        states = self.at(index)
        x = [0.]
        for first, second in zip(states, states[1:]):
            delta = find_mic(second.structure.positions - first.structure.positions, first.structure.cell, pbc=first.structure.pbc)[0]
            x.append(x[-1] + float(np.sqrt(np.mean(np.sum(delta ** 2, axis=1)))))
        return np.array(x)


def load(group, progress=lambda _text: None, stop=lambda: False, *, cache=None):
    if issues := group.problems():
        raise ValueError("\n".join(issues))
    images = []
    for item in group.images:
        if not item.included:
            continue
        if stop():
            return None
        progress(item.folder.name + " / " + item.source)
        image = load_image(item, stop)
        if cache is not None and not stop():
            progress(item.folder.name + " / 写入播放缓存")
            cache.add(image, stop)
        images.append(image)
    if stop():
        return None
    run = NebRun(group.root, images, group.config, selected=set(range(len(images))),
                 analysis_selected=set(range(len(images))),
                 opaque={index for index, image in enumerate(images) if image.endpoint}, cache=cache)
    run.active = next((index for index, image in enumerate(images) if image.count > 1), 0)
    run.at(0)
    return run
