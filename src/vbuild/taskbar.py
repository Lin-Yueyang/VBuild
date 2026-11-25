"""Identity and relaunch information used by the Windows taskbar."""

import ctypes as c
from pathlib import Path
from subprocess import list2cmdline
import sys
from uuid import UUID

from .paths import ROOT

APP_ID = "VBuild.StructureEditor"
GUID = c.c_ubyte * 16
STORE_IID = GUID.from_buffer_copy(UUID("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99").bytes_le)
APP_MODEL = GUID.from_buffer_copy(UUID("9f4c2855-9f79-4b39-a8d0-e1d42de1d5f3").bytes_le)


class PropertyKey(c.Structure):
    _fields_ = [("format_id", GUID), ("pid", c.c_uint32)]


class VariantData(c.Union):
    _fields_ = [("string", c.c_wchar_p), ("storage", c.c_void_p * 2)]


class PropVariant(c.Structure):
    _fields_ = [("type", c.c_ushort), ("reserved", c.c_ushort * 3), ("data", VariantData)]


def relaunch_target():
    launcher = ROOT / "VBuild.exe"
    if launcher.is_file():
        # The Windows launcher embeds its multi-resolution group icon at ID 32512.
        return list2cmdline([launcher]), f"{launcher},-32512"
    python = Path(sys.executable).with_name("pythonw.exe")
    script = ROOT / "gui.pyw"
    command = [python, script] if script.is_file() else [python, "-m", "vbuild", "gui"]
    icon = Path(__file__).with_name("icons") / "app.ico"
    return list2cmdline(command), f"{icon},0"


def set_window_identity(hwnd, *, clear=False):
    get_store = c.OleDLL("shell32").SHGetPropertyStoreForWindow
    get_store.argtypes = (c.c_void_p, c.POINTER(GUID), c.POINTER(c.c_void_p))
    store = c.c_void_p()
    get_store(hwnd, c.byref(STORE_IID), c.byref(store))
    set_value = c.WINFUNCTYPE(c.HRESULT, c.POINTER(PropertyKey), c.POINTER(PropVariant))(6, "SetValue")
    release = c.WINFUNCTYPE(c.c_ulong)(2, "Release")
    try:
        command, icon = relaunch_target()
        # Explorer reads the relaunch properties when the window ID (5) is set.
        for pid, value in ((2, command), (3, icon), (4, "VBuild"), (5, APP_ID)):
            variant = PropVariant()
            if not clear:
                variant.type = 31  # VT_LPWSTR; SetValue copies the supplied text.
                variant.data.string = value
            set_value(store, c.byref(PropertyKey(APP_MODEL, pid)), c.byref(variant))
    finally:
        release(store)
