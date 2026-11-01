"""Built-in portable styles, also available to the ordinary modeling viewport."""

from copy import deepcopy

from .look import default_style

TITLES = {"Default": "Default · 清晰", "塑料": "塑料球体", "橡胶": "橡胶哑光",
          "卡通": "卡通描边", "二维": "2D 圆片", "柔润": "柔润颗粒"}
DESCRIPTIONS = {
    "Default": "原清晰风格，作为默认。",
    "塑料": "硬质亮面、明确的白色高光、细圆柱键。",
    "橡胶": "无镜面反光、细微粗糙表面、饱满球体和细灰键。",
    "卡通": "分段明暗、深色轮廓、简化键。",
    "二维": "面向屏幕的真正圆片、纯色填充、轮廓与细线键。",
    "柔润": "柔润高光、透亮边缘和细微颗粒，近似果皮质感。",
}


def builtins():
    result = {}
    for name in TITLES:
        style = default_style()
        style.update(name=name, description=DESCRIPTIONS[name])
        lighting = {"Default": "illustration", "塑料": "plastic", "橡胶": "rubber", "卡通": "toon", "二维": "disc", "柔润": "fruit"}[name]
        style["scene"]["lighting"] = lighting
        style["scene"]["atom_scale"] = {"Default": 1., "塑料": 1.1, "橡胶": 1.25, "卡通": 1.1, "二维": 1.1, "柔润": 1.15}[name]
        if name in ("橡胶", "卡通", "二维"):
            style["bonds"].update(split=False, color="#52606D", radius=.065)
        if name == "塑料":
            style["bonds"]["radius"] = .075
        if name == "二维":
            style["bonds"].update(shape="line", width=3.)
        result[name] = style
    return result


def insert(library, profile):
    """Keep built-ins and distinct user styles when names collide."""
    base, value = profile["name"], deepcopy(profile)
    index = 1
    while value["name"] in library and library[value["name"]] != value:
        value["name"] = base + "（已保存" + (str(index) if index > 1 else "") + "）"
        index += 1
    library[value["name"]] = value
    return value
