"""Writable user data on macOS; preserve the existing Windows project layout."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]


def mac_user(root):
    return sys.platform == 'darwin' and Path(root).resolve() == ROOT


def work_dir(root=ROOT):
    return Path.home() / 'Library/Application Support/VBuild' if mac_user(root) else Path(root) / 'work'


def cache_dir(root=ROOT):
    return Path.home() / 'Library/Caches/VBuild' if mac_user(root) else work_dir(root) / 'cache'


def models_dir(root=ROOT):
    return work_dir(root) / 'MLIP' if mac_user(root) else Path(root) / 'MLIP'


def data_dir(root=ROOT):
    return work_dir(root) / 'data' if mac_user(root) else Path(root) / 'data'
