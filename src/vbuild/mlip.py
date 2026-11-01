"""Local MLIP registry, ordered by reference speed on Fe240 / RTX 5060."""

from dataclasses import dataclass
import sys

from .paths import ROOT, models_dir



@dataclass(frozen=True)
class Model:
    name: str
    file: str
    engine: str
    speed: float | None
    head: str | None = None

    @property
    def label(self):
        if sys.platform == 'darwin' or self.speed is None:
            return self.name
        return f"{self.name} · {self.speed:.1f}×" if self.speed == 1 else f"{self.name} · ≈{self.speed:.1f}×"

    @property
    def tooltip(self):
        if self.engine == 'deepmd':
            return 'OMol25 Neo；ASE 纯 PyTorch，float32。默认中性，偶数电子单重态、奇数电子双重态。尚无本机速度倍率。'
        if sys.platform == 'darwin':
            return 'NEP：CPU。' if self.engine == 'nep' else 'MACE：CPU / Apple GPU（MPS），float32。'
        text = ("速度参考：2026-10-04，RTX 5060，Fe240，ASE Langevin NVT 2000 步，"
                "MACE float32；MH-1 omat_pbe = 1.0×。不含加载和 GUI，实际速度随结构、设备及绘制改变。")
        return text + (" MP-0 倍率来自 L1 模型，当前附带的旧 MP-0 为 L0，仅供近似参考。" if self.name == "MACE MP-0" else "")

    @property
    def file_filter(self):
        if self.engine == 'deepmd':
            return 'DeePMD 检查点 (*.pt);;所有文件 (*)'
        return "NEP 模型 (*.txt);;所有文件 (*)" if self.engine == "nep" else "MACE 模型 (*.model);;所有文件 (*)"


MODELS = {"nep89": Model("NEP89", "NEP/nep89.txt", "nep", 49.3),
          "small-0b2": Model("MACE small-0b2", "MACE/small-0b2.model", "mace", 4.0),
          "mp-0": Model("MACE MP-0", "MACE/mp-0.model", "mace", 1.7),
          "mh-1": Model("MACE MH-1", "MACE/mh-1.model", "mace", 1.0, "omat_pbe"),
          "dpa4c-neo": Model('DPA4C OMol25 Neo', 'DeepMD/DPA4C-Neo-OMol25-100M-v20260820.pt', 'deepmd', None)}


def model_paths():
    return {name: models_dir() / model.file for name, model in MODELS.items()}


def default_multiplicity(symbols, charge=0):
    from ase.data import atomic_numbers
    return 1 + (sum(atomic_numbers[symbol] for symbol in symbols) - charge) % 2


def available_models(combo, paths):
    from PySide6.QtCore import Qt
    for index in range(combo.count()):
        name = combo.itemData(index)
        available = paths[name].is_file()
        combo.model().item(index).setEnabled(available)
        combo.setItemData(index, MODELS[name].tooltip if available else '未下载；在设置 → Python 与 MLIP 中下载。', Qt.ItemDataRole.ToolTipRole)
