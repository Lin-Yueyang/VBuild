"""ASE constraints are loaded only for calculation/import, then warmed in the background."""

from ase.constraints import FixConstraint

from .thermal import project


class AllowedMotion(FixConstraint):
    def __init__(self, cell, free):
        self.cell, self.free = cell.copy(), free.copy()

    def get_removed_dof(self, _atoms):
        return int((~self.free).sum())

    def adjust_positions(self, atoms, positions):
        positions[:] = atoms.positions + project(positions - atoms.positions, self.cell, self.free)

    def adjust_forces(self, _atoms, forces):
        forces[:] = project(forces, self.cell, self.free)

    def adjust_momenta(self, _atoms, momenta):
        momenta[:] = project(momenta, self.cell, self.free)
