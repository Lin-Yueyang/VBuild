"""Lazy NEP ASE adapter with explicit input checks and project-local CUDA cache."""

import os
import sys

from .paths import cache_dir


def prepare_backend():
    if sys.platform != 'darwin':
        cache = cache_dir() / 'cuda'
        cache.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("CUDA_CACHE_PATH", str(cache))
    os.environ.setdefault("OMP_NUM_THREADS", "4")


def calculator(path, device):
    prepare_backend()
    from nep_adapters.ase import NepAseCalculator

    class NepCalculator(NepAseCalculator):
        def calculate(self, atoms=None, properties=None, system_changes=None):
            if not atoms.pbc.all() or atoms.cell.rank != 3:
                raise ValueError("NEP 当前接口要求三维周期晶胞；请明确设置周期盒，或选择 MACE 模型。")
            missing = set(atoms.get_chemical_symbols()) - set(self._calc.element_list)
            if missing:
                raise ValueError("NEP 模型不支持元素：" + ", ".join(sorted(missing)))
            super().calculate(atoms, properties, system_changes)

    return NepCalculator(str(path), backend=device)
