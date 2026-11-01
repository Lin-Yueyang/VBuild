"""VASP real-space grids, with format-specific units and explicit spin channels."""

from dataclasses import dataclass, field
from pathlib import Path
import re

import numpy as np

from .poscar import PoscarDocument
from .source import identity, open_source
from .trajectory import coordinate_lines

GRID_NAMES = ("CHGCAR", "ELFCAR", "LOCPOT", "PARCHG", "AECCAR0", "AECCAR1", "AECCAR2", "CHG")


def grid_kind(path):
    name, suffix = Path(path).name.upper(), Path(path).suffix.upper().lstrip(".")
    return next((kind for kind in GRID_NAMES if name.startswith(kind) or suffix == kind), None)


def values(stream, count, stop):
    data, index = np.empty(count), 0
    while index < count:
        if stop():
            return None
        start = stream.tell()
        size = min(2 * 1024 * 1024, max(4096, (count - index) * 20))
        block = stream.read(size)
        if not block:
            raise ValueError("电子网格数据不足。")
        # Parse complete lines in C, retaining exact position before PAW / the next grid.
        end = block.rfind(b"\n") + 1 if len(block) == size else len(block)
        if not end:
            stream.seek(start)
            block = stream.readline()
            end = len(block)
        block = block[:end]
        tokens = block.split()
        remaining = count - index
        if len(tokens) > remaining:
            last = next(match for number, match in enumerate(re.finditer(rb"\S+", block), 1) if number == remaining)
            end = block.find(b"\n", last.end())
            end = len(block) if end == -1 else end + 1
            if block[last.end():end].strip():
                raise ValueError("电子网格数值/数量无效。")
            block, tokens = block[:end], tokens[:remaining]
        stream.seek(start + end)
        if not tokens:
            continue
        row = np.fromstring(block.replace(b"D", b"E").replace(b"d", b"e"), sep=" ")
        if len(row) != len(tokens) or not np.isfinite(row).all():
            raise ValueError("电子网格数值/数量无效。")
        data[index:index + len(row)] = row
        index += len(row)
    return data


@dataclass(eq=False)
class Volume:
    path: Path
    kind: str
    document: PoscarDocument
    channels: tuple[np.ndarray, ...]
    names: tuple[str, ...]
    unit: str
    atom_data: list = field(default_factory=list)

    @property
    def family(self):
        return "elf" if self.kind == "ELFCAR" else "charge"


def read_volume(path, stop=lambda: False):
    kind = grid_kind(path)
    stamp = identity(path)
    with open_source(path) as stream:
        header = [stream.readline() for _index in range(7)]
        count = sum(map(int, header[6].split()))
        mode = stream.readline()
        if mode.lower().startswith(b"s"):
            header.append(mode)
            mode = stream.readline()
        header.append(mode)
        document = PoscarDocument.from_bytes(b"".join(header) + b"".join(coordinate_lines(stream, count)))
        line = stream.readline()
        while not line.strip() and line:
            line = stream.readline()
        dimensions = tuple(map(int, line.split()))
        if len(dimensions) != 3 or min(dimensions) <= 0:
            raise ValueError("电子文件需要三个正整数 FFT 网格尺寸。")
        channels, atom_data = [], []
        while True:
            data = values(stream, int(np.prod(dimensions)), stop)
            if data is None:
                return None
            if kind not in ("ELFCAR", "LOCPOT"):
                data /= abs(np.linalg.det(document.structure.cell))
            channels.append(data.reshape(dimensions, order="F"))
            augmentation, auxiliary = False, []
            for line in stream:
                if stop():
                    return None
                tokens = line.split()
                if not tokens or tokens[0].startswith((b"#", b"!")):
                    continue
                if [token.lower() for token in tokens[:2]] == [b"augmentation", b"occupancies"]:
                    if len(tokens) != 4 or not 1 <= int(tokens[2]) <= count or int(tokens[3]) < 0:
                        raise ValueError("PAW augmentation 标记无效。")
                    if values(stream, int(tokens[-1]), stop) is None:
                        return None
                    augmentation = True
                elif len(tokens) == 3 and all(token.isdigit() for token in tokens) and tuple(map(int, tokens)) == dimensions:
                    if any(auxiliary):
                        if len(auxiliary) != count:
                            raise ValueError("电子原子辅助块的数值数量与原子数不一致。")
                        atom_data.append((len(channels) - 1, np.array(auxiliary)))
                    break
                elif not augmentation and all(re.fullmatch(rb"[+-]?(?:0+(?:\.0*)?|\.0+)(?:[EeDd][+-]?\d+)?", token) for token in tokens):
                    # VASP/AFLOW may put a numeric zero-padding line before the next spin grid.
                    continue
                elif augmentation and all(re.fullmatch(rb"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][+-]?\d+)?", token) for token in tokens):
                    # Some CHGCAR variants put one number per atom between PAW and the next FFT grid.
                    auxiliary.extend(float(token.replace(b"D", b"E").replace(b"d", b"e")) for token in tokens)
                    if not np.isfinite(auxiliary).all() or (any(auxiliary) and len(auxiliary) > count):
                        raise ValueError("电子原子辅助块数值/数量无效。")
                else:
                    raise ValueError(f"电子网格尾部有未识别的数据块（字节 {stream.tell() - len(line)}）：" + line.decode("ascii").strip()[:80])
            else:
                if any(auxiliary):
                    raise ValueError("电子原子辅助块之后缺少 FFT 网格尺寸。")
                break
    if identity(path) != stamp:
        raise ValueError("电子源文件在读取时发生变化，请重新导入。")
    if len(channels) not in (1, 2, 4) or (kind == "ELFCAR" and len(channels) == 4):
        raise ValueError("电子网格通道数量不符合单通道/共线/非共线格式。")
    if kind == "ELFCAR":
        names = ("ELF",) if len(channels) == 1 else ("ELF ↑", "ELF ↓")
        unit = "无量纲"
    elif kind == "LOCPOT":
        names = ("局域势",) if len(channels) == 1 else ("势 ↑", "势 ↓") if len(channels) == 2 else ("局域势（平均）", "B σ₁", "B σ₂", "B σ₃")
        unit = "eV"
    else:
        names = ("电荷密度",) if len(channels) == 1 else ("总电荷密度", "磁化密度") if len(channels) == 2 else ("总电荷密度", *("磁化分量 " + str(x) for x in range(1, len(channels))))
        unit = "1/Å³"
    return Volume(Path(path), kind, document, tuple(channels), tuple(names), unit, atom_data)
