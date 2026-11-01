"""Explicit import dispatch and background loading of external structure formats."""

from dataclasses import dataclass
from pathlib import Path
from xml.etree.ElementTree import ParseError

from PySide6.QtCore import QThread, Signal

from .formats import read_cif
from .reports import ReportTrajectory
from .source import open_source
from .volume import grid_kind, read_volume


def input_kind(path):
    path = Path(path)
    if path.name.lower().split(".", 1)[0] == "outcar":
        return "outcar"
    if grid_kind(path) is not None:
        return "volume"
    suffix = path.suffix.lower()
    if suffix == ".xml" or path.name.lower().startswith("vasprun.xml"):
        return "xml"
    if suffix in (".extxyz", ".entxyz"):
        return "extxyz"
    if suffix == ".xyz":
        with open_source(path) as stream:
            stream.readline()
            comment = stream.readline()
        return "extxyz" if b"Properties=" in comment or b"Lattice=" in comment else "xyz"
    if suffix in (".dump", ".lammpstrj"):
        return "dump"
    if suffix in (".data", ".lmp", ".lammps") or path.name.startswith("data."):
        return "lammps-data"
    if suffix == ".cif":
        return "cif"
    if suffix == ".xdatcar" or path.name.upper().startswith("XDATCAR"):
        return "xdatcar"
    return "vasp"


@dataclass
class Loaded:
    documents: list
    reader: object = None
    volume: object = None
    notice: str = ""
    readonly: bool = False


def read_input(path, kind, options, stop=lambda: False):
    if kind == "outcar":
        from .neb import outcar_probe, OutcarReader
        from .source import identity
        stamp = identity(path)
        seed = outcar_probe(path)
        reader = OutcarReader(path, stamp, seed, fixed_cell=False).index(stop)
        if stop():
            return None
        if identity(path) != stamp:
            raise ValueError("OUTCAR 在导入时已改变，请重新导入。")
        document = reader.frame(-1) if reader.offsets else seed
        if document.forces is not None:
            document.observations = document.observations.copy()
            if "max_force" in document.observations:
                document.observations["optimization_max_force"] = document.observations["max_force"]
            document.observations["max_force"] = document.observations["raw_max_force"]
        notice = "已导入 OUTCAR 的最后完整结构；点击“磁矩”查看原子磁矩。"
        return Loaded([document], notice=notice + reader.notice, readonly=True)
    if kind == "volume":
        volume = read_volume(path, stop)
        return None if volume is None else Loaded([volume.document], volume=volume)
    if kind in ("xml", "extxyz", "dump"):
        reader, document = ReportTrajectory.begin(path, kind, options.get("mapping"), options.get("units", "metal"))
        return Loaded([document], reader=reader)
    with open_source(path) as stream:
        content = stream.read()
    if kind == "cif":
        return Loaded(read_cif(content, path.name))
    from .lammps import read_data
    return Loaded([read_data(content, options["mapping"], options["units"], options["style"], options.get("pbc", (True, True, True)))])


class ImportThread(QThread):
    ready = Signal(object)
    failed = Signal(str)

    def __init__(self, path, kind, options, parent):
        super().__init__(parent)
        self.path, self.kind, self.options = path, kind, options

    def run(self):
        # External parser boundary: retain the source and report actual unsupported data.
        try:
            result = read_input(self.path, self.kind, self.options, self.isInterruptionRequested)
            if not self.isInterruptionRequested() and result is not None:
                self.ready.emit(result)
        except (OSError, ValueError, KeyError, IndexError, ParseError, RuntimeError, EOFError) as error:
            if not self.isInterruptionRequested():
                self.failed.emit(str(error))
