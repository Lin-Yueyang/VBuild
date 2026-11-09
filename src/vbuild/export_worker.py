"""Stream a fixed trajectory snapshot in a worker; publish only a complete file."""

from copy import copy, deepcopy
from dataclasses import replace
from pathlib import Path
from shutil import copyfile, move
from time import monotonic
from types import SimpleNamespace
import uuid
from xml.etree.ElementTree import ParseError

import numpy as np
from PySide6.QtCore import QThread, Signal

from .draft import DraftDocument
from .formats import FrameDocument, serialize
from .paths import work_dir
from .poscar import PoscarDocument
from .relax import Evolution
from .writing import group_order, vasp_header, vasp_rows


def snapshot(session):
    state = session.relaxation
    return SimpleNamespace(document=deepcopy(session.document), trajectory=copy(session.trajectory),
                           relaxation=None if state is None else replace(state, frames=tuple(state.frames), evolution=Evolution()))


def frame_count(session):
    if session.relaxation is not None:
        return len(session.relaxation.frames)
    reader = session.trajectory
    return reader.export_count if reader.kind == 'neb' else len(reader.offsets)


def frames(session, kind, interval=1):
    preferred = (session.document.original_labels if isinstance(session.document, PoscarDocument)
                 else session.document.original.labels if isinstance(session.document, DraftDocument) else ())
    previous = None
    for index in range(0, frame_count(session), interval):
        if session.relaxation is not None:
            structure = copy(session.document.structure)
            structure.positions = session.relaxation.frames[index].positions
            document = FrameDocument(structure, 'VBuild MLIP ' + session.relaxation.kind)
        else:
            reader = session.trajectory
            document = reader.export_frame(index) if reader.kind == 'neb' else reader.frame(index)
        structure = document.structure
        if kind == 'xyz':
            yield serialize(document, 'xyz')
            continue
        if structure.cell is None or not all(structure.pbc):
            raise ValueError('XDATCAR 需要每帧都有三维周期晶胞。')
        signature = (tuple(structure.labels), structure.cell.tobytes())
        header = b''
        if signature != previous:
            order, names, counts = group_order(structure.labels, preferred)
            header = ('\n'.join(vasp_header(structure.cell, names, counts, 'VBuild trajectory')) + '\n').encode()
            inverse = np.linalg.inv(structure.cell)
        previous = signature
        lines = f'Direct configuration= {index + 1}\n' + '\n'.join(vasp_rows(structure.positions[order] @ inverse)) + '\n'
        yield header + lines.encode()


class ExportTask(QThread):
    progress = Signal(int, int)
    phase = Signal(str)

    def __init__(self, session, target, kind, interval, parent, *, folder=None):
        super().__init__(parent)
        self.source = session
        self.snapshot = snapshot(session)
        self.target, self.kind, self.interval = Path(target), kind, interval
        self.total = (frame_count(self.snapshot) + interval - 1) // interval
        self.folder = (work_dir() / 'export' if folder is None else Path(folder)) / uuid.uuid4().hex[:8]
        self.error, self.cancelled, self.success = '', False, False
        self.backup = None

    def run(self):
        partial = self.target.with_name('.' + self.target.name + '.' + self.folder.name + '.part')
        try:
            self.folder.mkdir(parents=True)
            written, reported = 0, monotonic()
            with partial.open('xb') as stream:
                for content in frames(self.snapshot, self.kind, self.interval):
                    if self.isInterruptionRequested():
                        break
                    stream.write(content)
                    written += 1
                    if monotonic() - reported >= .1 or written == self.total:
                        self.progress.emit(written, self.total)
                        reported = monotonic()
            self.cancelled = self.isInterruptionRequested()
            if self.cancelled:
                return
            if self.target.exists():
                self.phase.emit('正在备份原文件…')
                self.backup = self.folder / self.target.name
                copyfile(self.target, self.backup)
            self.cancelled = self.isInterruptionRequested()
            if self.cancelled:
                return
            partial.replace(self.target)
            self.success = True
        except (OSError, ValueError, IndexError, KeyError, ParseError) as error:
            self.error = str(error)
        finally:
            if partial.exists():
                try:
                    move(str(partial), str(self.folder / 'unfinished.part'))
                except OSError as error:
                    self.error = str(error) + '\n未完成文件：' + str(partial)
