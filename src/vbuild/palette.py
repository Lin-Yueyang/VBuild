"""White-background palettes; conventional element families remain recognizable."""

import colorsys

import numpy as np

from .colors import element_colors


COLORS = {"H": "B9C2CE", "C": "364554", "N": "3459B7", "O": "CC3C4C",
                "F": "59A887", "P": "DA8F3E", "S": "D9B84A", "Cl": "4EAC79",
                "Li": "A58BC5", "Mg": "72B49A", "Al": "8B9BAC", "Si": "BAA080",
                "Ca": "298A69", "Fe": "D28A37", "Co": "647FC1", "Ni": "63A08F",
                "Cu": "BA7B36", "Zn": "8BA4B8", "Pt": "8E9AA8", "Bi": "955AA5"}


def palette():
    from ase.data import atomic_numbers

    colors = element_colors.copy()
    for index, value in enumerate(colors):
        hue, light, saturation = colorsys.rgb_to_hls(*value)
        colors[index] = colorsys.hls_to_rgb(hue, np.clip(light, .43, .68), min(saturation, .58))
    for symbol, value in COLORS.items():
        colors[atomic_numbers[symbol]] = [int(value[pos:pos+2], 16) / 255 for pos in (0, 2, 4)]
    return colors
