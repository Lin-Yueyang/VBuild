"""Portable, versioned appearance data. Styles contain no structure or executable code."""

from copy import deepcopy
import json
import re

import numpy as np
from ase.data import chemical_symbols, covalent_radii

from .palette import palette

KINDS = (("ball", "球棍模型"), ("space", "空间填充模型"),
         ("poly", "多面体模型"), ("stick", "棍状模型"))
LIGHTS = (("illustration", "清晰柔光"), ("plastic", "塑料球体"), ("rubber", "橡胶哑光"),
          ("toon", "卡通描边"), ("disc", "2D 圆片"), ("fruit", "柔润颗粒"),
          ("flat", "纯色球体"), ("soft", "柔和漫反射"), ("studio", "材质灯光"))


def color_rgb(value):
    return np.array([int(value[index:index + 2], 16) for index in (1, 3, 5)], dtype=np.uint8)


def default_style():
    elements = {}
    colors = palette()
    for number, symbol in enumerate(chemical_symbols[1:], 1):
        color = colors[number] * 255
        elements[symbol] = {"color": "#" + "".join(f"{round(channel):02X}" for channel in color),
                            "radius": float(covalent_radii[number] * 0.45),
                            "filling": float(covalent_radii[number]), "opacity": 1.,
                            "metallic": 0., "roughness": 0.9, "specular": 0.,
                            "show": True, "bonds": True, "representation": "inherit"}
    return {"format": "vbuild-style", "version": 3, "name": "Default", "author": "", "description": "",
            "representation": "ball", "elements": elements,
            "bonds": {"show": True, "shape": "cylinder", "radius": 0.12, "width": 15., "split": True,
                      "color": "#A7ADB5", "opacity": 1., "metallic": 0., "roughness": 0.9,
                      "factor": 1.15, "ranges": {}},
            "polyhedra": {"show": False, "centers": [], "neighbors": [], "opacity": 0.25, "edges": True},
            "scene": {"background": "#FFFFFF", "grid": False, "grid_color": "#D7DDE4", "grid_step": 2.,
                      "cell": True, "cell_color": "#778899", "label_color": "#000000", "axes": True, "parallel": True,
                      "key": 1.2, "fill": 0.6, "rim": 0.8, "lighting": "illustration", "ambient": 0.5,
                      "atom_scale": 1., "quality": 64},
            "image": {"scale": 2, "transparent": False, "guides": False}}


def number(value, minimum, maximum, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(label + "超出允许范围。")


def color(value):
    if not isinstance(value, str) or re.fullmatch(r"#[0-9a-fA-F]{6}", value) is None:
        raise ValueError("颜色必须是 #RRGGBB。")


def validate(style):
    template = default_style()
    if not isinstance(style, dict) or set(style) != set(template) or style["format"] != "vbuild-style" or type(style["version"]) is not int or style["version"] not in (1, 2, 3):
        raise ValueError("不是支持的 VBuild 样式文件（版本 1、2 或 3）。")
    if style["version"] == 1:
        style = migrate_v1(style, template)
    if style["version"] == 2:
        if style["scene"]["lighting"] not in ("soft", "studio"):
            raise ValueError("版本 2 样式的灯光模式无效。")
        style = deepcopy(style)
        style["version"] = 3
    for key in ("name", "author", "description"):
        if not isinstance(style[key], str):
            raise ValueError("样式名称、作者和说明必须是文字。")
    if not style["name"].strip() or not isinstance(style["representation"], str) or style["representation"] not in dict(KINDS):
        raise ValueError("样式名称或模型类型无效。")
    if not isinstance(style["elements"], dict) or set(style["elements"]) != set(template["elements"]):
        raise ValueError("样式需要包含全部 118 个元素。")
    for symbol, entry in style["elements"].items():
        validate_element(entry, template["elements"][symbol], symbol)
    for section in ("bonds", "polyhedra", "scene", "image"):
        if not isinstance(style[section], dict) or set(style[section]) != set(template[section]):
            raise ValueError("样式字段不完整：" + section)
    bond, scene, poly, image = (style[key] for key in ("bonds", "scene", "polyhedra", "image"))
    if bond["shape"] not in ("line", "cylinder"):
        raise ValueError("键类型必须是 line 或 cylinder。")
    for key in ("color",):
        color(bond[key])
    for key in ("background", "grid_color", "cell_color", "label_color"):
        color(scene[key])
    for key in ("opacity", "metallic", "roughness"):
        number(bond[key], 0, 1, "键 " + key)
    for key, limits in {"radius": (0.01, 5), "width": (1, 100), "factor": (0.1, 5)}.items():
        number(bond[key], *limits, "键 " + key)
    if not isinstance(bond["ranges"], dict):
        raise ValueError("键范围必须是元素对字典。")
    for pair, bounds in bond["ranges"].items():
        if not isinstance(pair, str) or not isinstance(bounds, list) or len(bounds) != 2:
            raise ValueError("键范围必须是元素对及两个距离组成的列表。")
        if len(pair.split("-")) != 2 or any(symbol not in style["elements"] for symbol in pair.split("-")):
            raise ValueError("键范围元素对无效。")
        number(bounds[0], 0, 100, pair)
        number(bounds[1], 0, 100, pair)
        if bounds[0] >= bounds[1]:
            raise ValueError("键最长距离必须大于最短距离。")
    for key in ("centers", "neighbors"):
        if not isinstance(poly[key], list) or any(not isinstance(symbol, str) or symbol not in style["elements"] for symbol in poly[key]):
            raise ValueError("多面体元素列表无效。")
    number(poly["opacity"], 0, 1, "多面体透明度")
    number(scene["grid_step"], 0.1, 100, "网格间距")
    number(scene["atom_scale"], 0.1, 5, "整体原子尺寸")
    number(scene["ambient"], 0, 1, "环境亮度")
    if scene["lighting"] not in dict(LIGHTS):
        raise ValueError("灯光模式无效。")
    if type(scene["quality"]) is not int or not 16 <= scene["quality"] <= 96:
        raise ValueError("球面细分必须为 16–96 的整数。")
    for key in ("key", "fill", "rim"):
        number(scene[key], 0, 5, "灯光强度")
    for section, keys in ((bond, ("show", "split")), (poly, ("edges", "show")),
                          (scene, ("grid", "cell", "axes", "parallel")), (image, ("transparent", "guides"))):
        if any(type(section[key]) is not bool for key in keys):
            raise ValueError("样式开关必须为布尔值。")
    if type(image["scale"]) is not int or not 1 <= image["scale"] <= 4:
        raise ValueError("图片倍率必须为 1–4 的整数。")
    return deepcopy(style)


def validate_element(entry, template, symbol):
    if not isinstance(entry, dict) or set(entry) != set(template):
        raise ValueError("元素样式字段不完整：" + symbol)
    color(entry["color"])
    for key in ("radius", "filling"):
        number(entry[key], 0.01, 10, symbol + " 半径")
    for key in ("opacity", "metallic", "roughness", "specular"):
        number(entry[key], 0, 1, symbol + " " + key)
    if any(type(entry[key]) is not bool for key in ("show", "bonds")) or entry["representation"] not in ("inherit", *dict(KINDS)):
        raise ValueError("元素显示开关或模型类型无效：" + symbol)


def migrate_v1(style, template):
    """Validate the old schema before adding fields; preserve its original lighting."""
    style = deepcopy(style)
    if not isinstance(style["elements"], dict) or set(style["elements"]) != set(template["elements"]):
        raise ValueError("旧样式需要包含全部 118 个元素。")
    for symbol, entry in style["elements"].items():
        if not isinstance(entry, dict) or set(entry) != set(template["elements"][symbol]) - {"show", "bonds", "representation"}:
            raise ValueError("旧样式元素字段不完整：" + symbol)
        entry.update(show=True, bonds=True, representation="inherit")
    for section, added in (("polyhedra", {"show"}), ("scene", {"lighting", "ambient"})):
        if not isinstance(style[section], dict) or set(style[section]) != set(template[section]) - added:
            raise ValueError("旧样式字段不完整：" + section)
    style["polyhedra"]["show"] = style["representation"] == "poly"
    style["scene"].update(lighting="studio", ambient=0.5)
    style["version"] = 2
    return style


def read_style(path):
    return validate(json.loads(path.read_text(encoding="utf-8-sig")))


def style_bytes(style):
    return (json.dumps(validate(style), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
