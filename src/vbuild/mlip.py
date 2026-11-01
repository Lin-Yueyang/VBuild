"""Local MLIP registry, ordered by reference speed on Fe240 / RTX 5060."""

from dataclasses import dataclass
import sys

from .paths import ROOT, models_dir



@dataclass(frozen=True)
class Model:
    name: str
    file: str
    engine: str
    speed: float
    head: str | None = None

    @property
    def label(self):
        if sys.platform == 'darwin':
            return self.name
        return f"{self.name} · {self.speed:.1f}×" if self.speed == 1 else f"{self.name} · ≈{self.speed:.1f}×"

    @property
    def tooltip(self):
        if sys.platform == 'darwin':
            return 'NEP：CPU。' if self.engine == 'nep' else 'MACE：CPU / Apple GPU（MPS），float32。'
        text = ("速度参考：2026-10-04，RTX 5060，Fe240，ASE Langevin NVT 2000 步，"
                "MACE float32；MH-1 omat_pbe = 1.0×。不含加载和 GUI，实际速度随结构、设备及绘制改变。")
        return text + (" MP-0 倍率来自 L1 模型，当前附带的旧 MP-0 为 L0，仅供近似参考。" if self.name == "MACE MP-0" else "")

    @property
    def file_filter(self):
        return "NEP 模型 (*.txt);;所有文件 (*)" if self.engine == "nep" else "MACE 模型 (*.model);;所有文件 (*)"


MODELS = {"nep89": Model("NEP89", "NEP/nep89.txt", "nep", 49.3),
          "small-0b2": Model("MACE small-0b2", "MACE/small-0b2.model", "mace", 4.0),
          "mp-0": Model("MACE MP-0", "MACE/mp-0.model", "mace", 1.7),
          "mh-1": Model("MACE MH-1", "MACE/mh-1.model", "mace", 1.0, "omat_pbe")}


def model_paths():
    return {name: models_dir() / model.file for name, model in MODELS.items()}
