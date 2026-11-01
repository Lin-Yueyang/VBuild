"""Small asynchronous client for the official Materials Project summary REST API."""

import json
import re
from urllib.parse import urlencode

import numpy as np
from ase.data import atomic_numbers
from ase.formula import Formula
from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from .draft import validate_cell
from .poscar import PoscarDocument

ENDPOINT = "https://api.materialsproject.org/materials/summary/"
PAGE_SIZE = 50
FIELDS = "material_id,formula_pretty,nsites,symmetry,energy_above_hull,band_gap,is_stable"


def summary_query(symbol, offset=0):
    symbol = symbol.strip()
    counts = Formula(symbol).count()
    if not counts or any(element not in atomic_numbers or element == "X" or count <= 0 for element, count in counts.items()):
        raise ValueError("请输入有效元素或化学式，如 Fe、Fe2O3。")
    parameter = "chemsys" if symbol in atomic_numbers else "formula"
    return {parameter: symbol, "deprecated": "false",
            "_fields": FIELDS, "_limit": PAGE_SIZE, "_skip": offset, "_sort_fields": "energy_above_hull"}


def structure_poscar(record):
    material_id = record["material_id"]
    if re.fullmatch(r"mp-[A-Za-z0-9]+", material_id) is None:
        raise ValueError("Materials Project 返回了无法识别的材料编号。")
    structure = record["structure"]
    cell = np.array(structure["lattice"]["matrix"], dtype=float)
    validate_cell(cell)
    symbols, coordinates = [], []
    for site in structure["sites"]:
        species = site["species"]
        if len(species) != 1 or abs(float(species[0]["occu"]) - 1) > 1e-8:
            raise ValueError("该结构包含混占/部分占位，不能直接作为确定原子的 POSCAR 打开。")
        symbol = species[0]["element"]
        if symbol not in atomic_numbers or symbol == "X":
            raise ValueError("结构包含无法识别的元素。")
        symbols.append(symbol)
        coordinates.append(site["abc"])
    points = np.asarray(coordinates, dtype=float)
    if not symbols or points.shape != (len(symbols), 3) or not np.isfinite(points).all():
        raise ValueError("下载结构的坐标数量或数值无效。")
    groups, counts = [], []
    for symbol in symbols:
        if groups and groups[-1] == symbol:
            counts[-1] += 1
        else:
            groups.append(symbol)
            counts.append(1)
    lines = ["Materials Project " + material_id, "1.0",
             *(" ".join(format(value, ".17g") for value in row) for row in cell),
             " ".join(groups), " ".join(map(str, counts)), "Direct",
             *(" ".join(format(value, ".17g") for value in row) for row in points)]
    content = ("\n".join(lines) + "\n").encode("utf-8")
    PoscarDocument.from_bytes(content)
    return content


class MaterialsClient(QObject):
    ready = Signal(object)
    failed = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.manager = QNetworkAccessManager(self)
        self.reply = None

    def fetch(self, params, key):
        if not key.strip():
            self.failed.emit("请填写 Materials Project API key，再点击搜索。")
            return
        if not key.isascii():
            self.failed.emit("API key 只能包含 ASCII 字符。")
            return
        request = QNetworkRequest(QUrl(ENDPOINT + "?" + urlencode({**params, "id_format": "legacy"})))
        request.setRawHeader(b"X-API-KEY", key.strip().encode("ascii"))
        request.setRawHeader(b"Accept", b"application/json")
        request.setTransferTimeout(30000)
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.SameOriginRedirectPolicy)
        self.reply = self.manager.get(request)
        self.busy_changed.emit(True)
        self.reply.finished.connect(self.finish)

    def cancel(self):
        if self.reply is not None:
            self.reply.abort()

    def finish(self):
        reply, self.reply = self.reply, None
        self.busy_changed.emit(False)
        if reply.error() != QNetworkReply.NetworkError.NoError:
            self.failed.emit("Materials Project 请求失败：" + reply.errorString())
        else:
            # Remote JSON is an input boundary; show format errors without substituting data.
            try:
                payload = json.loads(bytes(reply.readAll()))
                data, total = payload["data"], int(payload["meta"]["total_doc"])
                if not isinstance(data, list):
                    raise ValueError("API 的 data 字段不是结果列表。")
            except (ValueError, TypeError, KeyError, UnicodeError) as error:
                self.failed.emit("Materials Project 响应格式错误：" + str(error))
            else:
                self.ready.emit((data, total))
        reply.deleteLater()
