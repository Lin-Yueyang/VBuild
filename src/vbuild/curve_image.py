"""Export selected MD curves with their displayed axes and an external legend."""

import numpy as np

LABELS = {'temperature': ('Temperature (K)', '#e88520'),
          'force': ('Max atomic force (eV/Å)', '#42a077'),
          'energy': ('Potential energy (eV)', '#367ac1')}


def figure(times, series, channels, x_range, ranges, *, x_label='Time (fs)'):
    from matplotlib.figure import Figure

    image = Figure(figsize=(10, 6))
    image.subplots_adjust(left=.11, right=.76, bottom=.13, top=.80)
    base = image.add_subplot()
    base.set_xlabel(x_label)
    base.set_xlim(x_range)
    base.grid(alpha=.18)
    handles, axes = [], []
    for key in LABELS:
        if key not in channels:
            continue
        axis = base if not axes else base.twinx()
        if len(axes) > 1:
            axis.spines['right'].set_position(('axes', 1.18))
        label, color = LABELS[key]
        y = np.asarray(series[key])
        handle, = axis.plot(times, y, color=color, linewidth=1.3, label=label)
        axis.set_ylabel(label, color=color)
        axis.tick_params(axis='y', colors=color)
        axis.set_ylim(ranges[key])
        axes.append(axis)
        handles.append(handle)
    image.legend(handles=handles, loc='upper center', bbox_to_anchor=(.5, .96),
                 ncols=len(handles), frameon=False)
    return image
