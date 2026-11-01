"""Standalone helper copied outside the live source before the GUI closes."""

import argparse
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import subprocess
import sys

from update_files import install


def wait_for_gui(pid):
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.OpenProcess.argtypes, api.OpenProcess.restype = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = api.OpenProcess(0x100000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:
            return
        raise ctypes.WinError(ctypes.get_last_error())
    result = api.WaitForSingleObject(handle, 120000)
    api.CloseHandle(handle)
    if result != 0:
        raise OSError('软件尚未退出，更新未执行。')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--wait', type=int, default=0)
    parser.add_argument('--restart', action='store_true')
    args = parser.parse_args()
    try:
        if args.wait:
            wait_for_gui(args.wait)
        result = install(args.stage, args.root)
        (args.stage.parent / 'installed.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    except (OSError, ValueError, KeyError) as error:
        (args.stage.parent / 'install-error.txt').write_text(str(error), encoding='utf-8')
        if args.restart:
            ctypes.windll.user32.MessageBoxW(None, '更新失败，旧程序已保留。\n' + str(error), 'VBuild 更新', 0)
        return 1
    if args.restart:
        subprocess.Popen([str(args.root / 'VBuild.exe')], cwd=args.root, creationflags=0x08000000)


if __name__ == '__main__':
    sys.exit(main())
