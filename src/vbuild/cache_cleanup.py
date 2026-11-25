"""Reclaim abandoned NEB grids and unfinished export files in the workspace."""

from pathlib import Path
import re
import stat
import time

from PySide6.QtCore import QLockFile


def cache_lock(folder):
    lock = QLockFile(str(folder.with_name("." + folder.name + ".lock")))
    lock.setStaleLockTime(0)
    return lock


def plain(path, directory=False):
    info = path.lstat()
    tag = getattr(info, "st_reparse_tag", 0)
    return not (tag & 0x20000000) and (stat.S_ISDIR if directory else stat.S_ISREG)(info.st_mode)


def clean(work, days=None):
    cutoff = time.time() - days * 86400 if days is not None else float("inf")
    count = size = busy = 0
    errors = []
    for area, pattern in (("cache", r"neb_[0-9a-f]{12}"), ("export", r"[0-9a-f]{8}")):
        root = Path(work) / area
        if not root.exists() or not plain(root, directory=True):
            continue
        for folder in root.iterdir():
            if not re.fullmatch(pattern, folder.name):
                continue
            try:
                if not plain(folder, directory=True):
                    continue
                if area == "export":
                    partial = folder / "unfinished.part"
                    if not partial.is_file():
                        continue
                elif days is not None and not folder.with_name("." + folder.name + ".lock").exists():
                    # Older releases did not mark active NEB pages. Only clean those on explicit request.
                    continue
                if folder.stat().st_mtime > cutoff:
                    continue
                lock = cache_lock(folder)
                if not lock.tryLock(0):
                    busy += 1
                    continue
                try:
                    files = list(folder.iterdir()) if area == "cache" else [partial]
                    if any(not plain(p) or (area == "cache" and not re.fullmatch(r"[0-9]+\.bin", p.name))
                           or p.stat().st_mtime > cutoff for p in files):
                        continue
                    for p in files:
                        length = p.stat().st_size
                        p.unlink()
                        count += 1
                        size += length
                    if not any(folder.iterdir()):
                        folder.rmdir()
                finally:
                    lock.unlock()
            except FileNotFoundError:
                continue
            except OSError as error:
                errors.append(str(error))
    return count, size, busy, errors
