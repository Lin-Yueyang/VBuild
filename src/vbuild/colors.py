"""Jmol colors through Mt; explicit neutral colors for Ds through Og."""

import numpy as np
from ase.data.colors import jmol_colors

element_colors = np.vstack((jmol_colors, np.tile([0.72, 0.76, 0.82], (9, 1))))

# Display material classification: exclude nonmetals and metalloids from metal shine.
nonmetals = {1, 2, 5, 6, 7, 8, 9, 10, 14, 15, 16, 17, 18, 32, 33, 34, 35, 36,
             51, 52, 53, 54, 85, 86, 117, 118}
