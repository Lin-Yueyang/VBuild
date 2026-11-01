"""Materials Project key in Windows user environment or macOS Keychain."""

import os
import sys


def read():
    if sys.platform == 'darwin':
        from .mac_key import read as read_key
        key = read_key()
        return os.environ.get('MP_API_KEY', '') if key is None else key
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as environment:
                return winreg.QueryValueEx(environment, "MP_API_KEY")[0]
        except FileNotFoundError:
            pass
    return os.environ.get("MP_API_KEY", "")


def save(key):
    if not key or not key.isascii() or any(ord(char) < 33 or ord(char) > 126 for char in key):
        raise ValueError("API key 只能包含非空的 ASCII 可见字符。")
    persistent = sys.platform in ('win32', 'darwin')
    if sys.platform == 'darwin':
        from .mac_key import save as save_key
        save_key(key)
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as environment:
            winreg.SetValueEx(environment, "MP_API_KEY", 0, winreg.REG_SZ, key)
        notify = ctypes.windll.user32.SendMessageTimeoutW
        notify.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPCWSTR,
                           wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t))
        notify.restype = wintypes.LPARAM
        notify(0xFFFF, 0x001A, 0, "Environment", 0x0002, 1000, None)
    os.environ["MP_API_KEY"] = key
    return persistent
