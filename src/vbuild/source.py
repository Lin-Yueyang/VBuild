"""Short-lived reads that allow Windows source files to be moved or removed."""

import os
from pathlib import Path


def open_source(path):
    if os.name != "nt":
        return Path(path).open("rb")
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    handle = create(str(Path(path).resolve()), 0x80000000, 7, None, 3, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    return os.fdopen(descriptor, "rb", buffering=1024 * 1024)


def identity(path):
    stat = Path(path).stat()
    return stat.st_size, stat.st_mtime_ns, stat.st_ino
