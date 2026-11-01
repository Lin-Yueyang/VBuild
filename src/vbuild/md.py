"""Fixed-cell NVT pre-equilibration with constrained thermal velocities."""

from time import perf_counter

import numpy as np
from ase import Atoms, units

from .relax import Step
from .thermal import generate, temperature, project
from .walls import WallCalculator, Walls


def equilibrate(structure, calculator, target, steps, timestep, friction, receive,
                stop=lambda: False, seed=None, walls=Walls()):
    from .motion import AllowedMotion
    from ase.md.langevin import Langevin
    if structure.free is not None and not structure.free.any():
        raise ValueError("全部原子固定，请先解除需要预平衡的原子约束。")
    atoms = Atoms(symbols=structure.symbols, positions=structure.positions,
                  cell=structure.cell, pbc=structure.pbc)
    # A nonperiodic XYZ uses Cartesian directions for constraint projection only.
    basis = np.eye(3) if structure.cell is None else structure.cell
    if structure.free is not None:
        atoms.set_constraint(AllowedMotion(basis, structure.free))
    atoms.calc = WallCalculator(calculator, walls, basis, structure.free, len(atoms))
    velocities = generate(structure.symbols, basis, structure.free, target, seed)
    atoms.set_velocities(velocities / units.fs)
    dynamics = Langevin(atoms, timestep * units.fs, temperature_K=target,
                        friction=friction / units.fs, fixcm=False,
                        rng=np.random.default_rng(seed), logfile=None)
    started = perf_counter()
    for _finished in dynamics.irun(steps):
        energy = float(atoms.get_potential_energy())
        raw = atoms.get_forces(apply_constraint=False)
        values = atoms.get_velocities() * units.fs
        if not np.isfinite(energy) or not np.isfinite(raw).all() or not np.isfinite(values).all():
            raise ValueError("模型返回非有限能量、力或速度，已停止预平衡。")
        force = project(raw, basis, structure.free)
        receive(Step(atoms.positions.copy(), dynamics.nsteps, energy,
                     float(np.linalg.norm(force, axis=1).max()),
                     float(np.linalg.norm(raw, axis=1).max()), perf_counter() - started,
                     dynamics.nsteps * timestep,
                     temperature(structure.symbols, values, basis, structure.free),
                     float(atoms.get_kinetic_energy()), values.copy()))
        if stop():
            return "已停止"
    return "预平衡完成"
