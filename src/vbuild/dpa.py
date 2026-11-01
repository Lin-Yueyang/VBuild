"""DPA4C checkpoint inference through ASE, in its own compute process."""

import os

import numpy as np
from ase.calculators.calculator import Calculator, all_changes


class DpaCalculator(Calculator):
    implemented_properties = ['energy', 'forces', 'stress']

    def __init__(self, path, device, charge_spin=(0, 1)):
        super().__init__()
        os.environ['DEVICE'] = device
        os.environ['NUM_WORKERS'] = '0'
        from deepmd.calculator import DP
        self.backend = DP(model=str(path))
        self.charge_spin = np.asarray(charge_spin, dtype=np.float32)

    def calculate(self, atoms=None, properties=('energy', 'forces'), system_changes=all_changes):
        super().calculate(atoms, properties, system_changes)
        snapshot = self.atoms.copy()
        snapshot.info['charge_spin'] = self.charge_spin.copy()
        self.backend.calculate(snapshot, properties, system_changes)
        self.results = {key: value for key, value in self.backend.results.items()
                        if key in self.implemented_properties}
