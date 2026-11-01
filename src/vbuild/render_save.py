"""Self-contained rendering scenes, separate from reusable element style files."""

from dataclasses import dataclass
import json

import numpy as np
from ase.data import atomic_numbers

from .draft import DraftDocument, validate_cell
from .look import number, validate, validate_element
from .model import Structure, Velocities


@dataclass
class SavedScene:
    document: DraftDocument
    style: dict
    atoms: dict
    view: dict


def array(value, shape, label, *, boolean=False):
    result = np.asarray(value)
    if result.size == 0:
        result = result.reshape(shape).astype(bool if boolean else float)
    allowed = result.dtype.kind == "b" if boolean else result.dtype.kind in "iuf"
    if result.shape != shape or not allowed or not np.isfinite(result).all():
        raise ValueError(label + "的数量或数值无效。")
    return result.copy() if boolean else result.astype(float)


def fields(data, keys, label):
    if not isinstance(data, dict) or set(data) != set(keys.split()):
        raise ValueError(label + "字段不完整。")


def read_data(data):
    fields(data, "format version document style atoms view", "场景")
    if data["format"] != "vbuild-scene" or type(data["version"]) is not int or data["version"] != 1:
        raise ValueError("不是支持的 VBuild 渲染场景（版本 1）。")
    raw = data["document"]
    fields(raw, "title symbols positions cell free temperature temperature_pending labels origins periodic velocities", "场景结构")
    symbols = raw["symbols"]
    if not isinstance(symbols, list) or any(not isinstance(symbol, str) or symbol not in atomic_numbers or symbol == "X" for symbol in symbols):
        raise ValueError("场景元素无效。")
    count = len(symbols)
    points = array(raw["positions"], (count, 3), "坐标")
    cell = None if raw["cell"] is None else array(raw["cell"], (3, 3), "晶胞")
    if cell is not None:
        validate_cell(cell)
    free = None if raw["free"] is None else array(raw["free"], (count, 3), "约束", boolean=True)
    periodic = raw["periodic"]
    if not isinstance(periodic, list) or len(periodic) != 3 or any(type(value) is not bool for value in periodic) or (cell is None and any(periodic)):
        raise ValueError("场景周期边界无效。")
    labels, origins = raw["labels"], raw["origins"]
    if not isinstance(labels, list) or len(labels) != count or any(not isinstance(value, str) or not value.strip() for value in labels):
        raise ValueError("场景原子标签无效。")
    if not isinstance(origins, list) or len(origins) != count or any(value is not None and (type(value) is not int or value < 0) for value in origins):
        raise ValueError("场景原子来源映射无效。")
    if not isinstance(raw["title"], str) or type(raw["temperature_pending"]) is not bool:
        raise ValueError("场景标题或温度状态无效。")
    if raw["temperature"] is not None:
        number(raw["temperature"], 0, 1e9, "温度")
    structure = Structure(tuple(symbols), cell, points, free, raw["temperature"], tuple(labels), tuple(origins),
                          raw["temperature_pending"], tuple(periodic))
    document = DraftDocument(structure, raw["title"], unsaved=False)
    velocities = raw["velocities"]
    if velocities is not None:
        fields(velocities, "mode values", "场景速度")
        if velocities["mode"] not in ("cartesian", "direct"):
            raise ValueError("场景速度坐标类型无效。")
        document.input_velocities = Velocities(array(velocities["values"], (count, 3), "速度"), velocities["mode"])
    style = validate(data["style"])
    if not isinstance(data["atoms"], dict):
        raise ValueError("单原子外观必须是编号字典。")
    atoms = {}
    for key, entry in data["atoms"].items():
        if not isinstance(key, str) or not key.isdecimal() or str(int(key)) != key or not 0 <= int(key) < count:
            raise ValueError("单原子外观编号超出当前结构。")
        index = int(key)
        base = style["elements"][symbols[index]]
        if not isinstance(entry, dict) or not set(entry) <= set(base):
            raise ValueError("单原子外观字段无效。")
        validate_element(base | entry, base, symbols[index])
        atoms[index] = entry.copy()
    view = data["view"]
    fields(view, "camera mode selection show_periodic periods show_indices hidden_elements show_fixed index_font", "场景视图")
    if view["mode"] not in ("rotate", "select", "edit", "distance", "angle", "dihedral", "plane"):
        raise ValueError("场景操作模式无效。")
    if not isinstance(view["selection"], list) or any(type(index) is not int or not 0 <= index < count for index in view["selection"]):
        raise ValueError("场景选区无效。")
    if any(type(view[key]) is not bool for key in ("show_periodic", "show_indices", "show_fixed")):
        raise ValueError("场景视图开关无效。")
    if not isinstance(view["periods"], list) or len(view["periods"]) != 3 or any(type(value) is not int or not 1 <= value <= 10 for value in view["periods"]):
        raise ValueError("场景周期显示数量无效。")
    if not isinstance(view["hidden_elements"], list) or any(not isinstance(value, str) or value not in style["elements"] for value in view["hidden_elements"]):
        raise ValueError("场景序号筛选无效。")
    if type(view["index_font"]) is not int or not 8 <= view["index_font"] <= 48:
        raise ValueError("场景序号字号无效。")
    camera = view["camera"]
    fields(camera, "position focal_point up parallel_scale view_angle parallel_projection window_center reflection", "场景相机")
    position, focus, up = (array(camera[key], (3,), "相机 " + key) for key in ("position", "focal_point", "up"))
    if np.linalg.norm(position - focus) < 1e-10 or np.linalg.norm(np.cross(position - focus, up)) < 1e-10:
        raise ValueError("场景相机方向无效。")
    array(camera["window_center"], (2,), "相机窗口中心")
    number(camera["parallel_scale"], 1e-10, 1e12, "相机比例")
    number(camera["view_angle"], 0.01, 179, "相机视角")
    if type(camera["parallel_projection"]) is not bool or type(camera["reflection"]) not in (int, float) or camera["reflection"] not in (-1, 1):
        raise ValueError("场景相机投影无效。")
    return SavedScene(document, style, atoms, view)


def scene_bytes(document, style, atoms, view):
    structure, velocities = document.structure, document.input_velocities
    data = {"format": "vbuild-scene", "version": 1, "style": style,
            "atoms": {str(index): values for index, values in atoms.items()}, "view": view,
            "document": {"title": document.title, "symbols": list(structure.symbols),
                         "positions": structure.positions.tolist(), "cell": None if structure.cell is None else structure.cell.tolist(),
                         "free": None if structure.free is None else structure.free.tolist(), "temperature": structure.temperature,
                         "temperature_pending": structure.temperature_pending, "labels": list(structure.labels),
                         "origins": list(structure.origins), "periodic": list(structure.pbc),
                         "velocities": None if velocities is None else {"mode": velocities.mode, "values": velocities.values.tolist()}}}
    read_data(data)
    return (json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def read_scene(path):
    return read_data(json.loads(path.read_text(encoding="utf-8-sig")))
