"""Explicit RDKit embedding and force-field choice for a molecule snapshot."""

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Molecule:
    symbols: tuple[str, ...]
    positions: np.ndarray
    formula: str


def generate(text, forcefield="MMFF94s"):
    from rdkit import Chem
    from rdkit.Chem import AllChem, rdMolDescriptors

    molecule = Chem.MolFromSmiles(text.strip())
    if molecule is None or molecule.GetNumAtoms() == 0:
        raise ValueError("SMILES 无效或为空。")
    if len(Chem.GetMolFrags(molecule)) != 1:
        raise ValueError("请输入单个连通分子的 SMILES；多个独立片段请分别添加。")
    molecule = Chem.AddHs(molecule)
    if molecule.GetNumAtoms() > 500:
        raise ValueError("本入口每个分子最多 500 个原子（包括氢）。")
    params = AllChem.ETKDGv3()
    params.randomSeed = 0xF00D
    params.numThreads = 1
    if AllChem.EmbedMolecule(molecule, params) != 0:
        raise ValueError("SMILES 三维构象生成失败，请检查结构。")
    if forcefield == "MMFF94s":
        if not AllChem.MMFFHasAllMoleculeParams(molecule):
            raise ValueError("MMFF94s 缺少该分子的参数，可手动选择 UFF 再预览。")
        status = AllChem.MMFFOptimizeMolecule(molecule, mmffVariant="MMFF94s", maxIters=1000)
    elif forcefield == "UFF":
        if not AllChem.UFFHasAllMoleculeParams(molecule):
            raise ValueError("UFF 缺少该分子的参数，不能优化。")
        status = AllChem.UFFOptimizeMolecule(molecule, maxIters=1000)
    else:
        raise ValueError("未知分子力场。")
    if status != 0:
        raise ValueError("分子力场优化未收敛，未复制到剪贴板。")
    positions = np.asarray(molecule.GetConformer().GetPositions())
    positions -= positions.mean(axis=0)
    return Molecule(tuple(atom.GetSymbol() for atom in molecule.GetAtoms()), positions,
                    rdMolDescriptors.CalcMolFormula(molecule))
