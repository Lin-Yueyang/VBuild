"""Explicit compute devices; Apple's GPU is available to MACE, not NEP."""

import sys


def gpu_device():
    return 'mps' if sys.platform == 'darwin' else 'cuda'


def has_gpu_backend(engine):
    return sys.platform != 'darwin' or engine == 'mace'


def install_device(engine, requested):
    # The macOS installer explicitly labels its GPU option as MACE-only.
    return 'cpu' if requested == 'mps' and engine == 'nep' else requested


def check_device(engine, device):
    if device not in ('cpu', gpu_device()) or device != 'cpu' and not has_gpu_backend(engine):
        raise ValueError(f'{engine.upper()} 不支持所选设备 {device}；请重新选择设备。')
