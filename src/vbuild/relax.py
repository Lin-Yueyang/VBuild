"""Fixed-cell relaxation, physical constraint projection and cached step frames."""

from dataclasses import dataclass, field
from time import perf_counter

import numpy as np
from ase import Atoms

from .walls import Walls
from .writing import group_order, vasp_header, vasp_rows


@dataclass
class Step:
    positions: np.ndarray
    number: int = 0
    energy: float | None = None
    force: float | None = None
    raw_force: float | None = None
    elapsed: float = 0
    time_fs: float = 0
    temperature: float | None = None
    kinetic: float | None = None
    velocities: np.ndarray | None = None


@dataclass
class Relaxation:
    frames: list[Step] = field(default_factory=list)
    fmax: float = 0.05
    max_steps: int = 200
    running: bool = False
    status: str = "请解除需要优化的原子约束"
    kind: str = "opt"
    original_free: np.ndarray | None = None
    temperature: float = 300
    timestep: float = 1
    friction: float = 0.01
    walls: Walls = field(default_factory=Walls)

    def reset(self, positions):
        self.frames = [Step(positions.copy())]


def optimize(structure, calculator, fmax, max_steps, receive, stop=lambda: False):
    from .motion import AllowedMotion
    from ase.optimize import FIRE
    if structure.free is not None and not structure.free.any():
        raise ValueError("全部原子固定，请先解除需要优化的原子约束。")
    atoms = Atoms(symbols=structure.symbols, positions=structure.positions,
                  cell=structure.cell, pbc=structure.pbc)
    if structure.free is not None:
        atoms.set_constraint(AllowedMotion(structure.cell, structure.free))
    atoms.calc = calculator
    dynamics = FIRE(atoms, logfile=None, maxstep=0.1)
    started = perf_counter()
    for converged in dynamics.irun(fmax=fmax, steps=max_steps):
        energy = float(atoms.get_potential_energy())
        forces, raw = atoms.get_forces(), atoms.get_forces(apply_constraint=False)
        if not np.isfinite(energy) or not np.isfinite(raw).all():
            raise ValueError("模型返回非有限能量或力，已停止优化。")
        receive(Step(atoms.positions.copy(), dynamics.nsteps, energy,
                     float(np.linalg.norm(forces, axis=1).max()),
                     float(np.linalg.norm(raw, axis=1).max()), perf_counter() - started))
        if stop():
            return "已停止"
    return "已收敛" if converged else "达到最大步数"


def xdatcar_chunks(structure, frames, *, interval=1):
    if structure.cell is None:
        raise ValueError("XYZ 没有晶胞，无法导出 XDATCAR。")
    order, labels, counts = group_order(structure.labels)
    header = vasp_header(structure.cell, labels, counts, "VBuild trajectory")
    inverse = np.linalg.inv(structure.cell)
    yield ("\n".join(header) + "\n").encode("utf-8")
    for index in range(0, len(frames), interval):
        step = frames[index]
        lines = [f"Direct configuration= {index + 1:6d}"]
        lines += vasp_rows(step.positions[order] @ inverse)
        yield ("\n".join(lines) + "\n").encode("utf-8")


def xdatcar(structure, frames):
    return b"".join(xdatcar_chunks(structure, frames))
