"""Display-only energy conversions; calculation records always remain in eV."""

import numpy as np
from ase import units

ENERGY_UNITS = {'eV': 1., 'meV': 1000., 'Hartree': 1 / units.Hartree,
                'kJ/mol': units.mol / units.kJ, 'kcal/mol': units.mol / units.kcal}


def energy_values(values, unit='eV', origin=0.):
    return (np.asarray(values) - origin) * ENERGY_UNITS[unit]


def energy_label(unit='eV', relative=False, *, english=False):
    title = ('Potential energy change' if relative else 'Potential energy') if english else ('势能变化' if relative else '势能')
    return f'{title} ({unit})'
