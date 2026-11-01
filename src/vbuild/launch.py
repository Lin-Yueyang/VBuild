"""GUI startup boundary with native missing-library messages and persistent logs."""

import ctypes
from datetime import datetime
import importlib.metadata as metadata
import os
from pathlib import Path
import shlex
import subprocess
import sys
import struct
import platform
import sysconfig
import traceback
from time import perf_counter

from .runtime import supported, CATALOG, version_problems, gui_plan, process_env
from .paths import work_dir

ROOT = Path(__file__).resolve().parents[2]
GUI = ("numpy", "ase", "scipy", "PySide6", "pyvista", "pyvistaqt", "vtk", "spglib", "matplotlib")
MACE = ("torch", "mace-torch")
NEP = ("nep-adapters",)


def versions(names):
    found, missing = {}, []
    for name in names:
        try:
            found[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            missing.append(name)
    return found, missing


def console_python():
    executable = Path(sys.executable)
    return executable.with_name("python.exe") if executable.name.lower() == "pythonw.exe" else executable


def missing_message(missing, extra="gui"):
    torch_note = "\n\n请先按 docs/launch.md 手动安装 CPU 或 GPU 版 PyTorch。" if extra == "relax" else ""
    command = (f'& "{console_python()}" -m pip install "{ROOT}[{extra}]"' if sys.platform == "win32"
               else f'{shlex.quote(str(console_python()))} -m pip install {shlex.quote(str(ROOT) + "[" + extra + "]")}')
    terminal = "PowerShell" if sys.platform == "win32" else "终端"
    return ("所选 Python 缺少依赖：" + ", ".join(missing)
            + f'\n\nPython：{console_python()}\n\n请在{terminal}中手动安装：\n' + command
            + torch_note + "\n\n可在“设置→Python 与 MLIP”选择 Python、下载模型与计算依赖。")


def message(text):
    if sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(None, text, "VBuild", 0x10)
    elif sys.platform == "darwin":
        script = 'on run argv\n display alert "VBuild" message (item 1 of argv) as critical\nend run'
        subprocess.run(["osascript", "-e", script, text], check=True)
    else:
        print(text, file=sys.__stderr__)


def offer_gui_install(found, files):
    text = ('需要安装建模依赖：\n' + '、'.join(gui_plan(found)) + '\n\nPython：' + str(console_python())
            + '\nQt/VTK 体积较大；此步骤不安装 MLIP 或 CUDA。\n安装完成后自动打开软件。')
    if sys.platform == 'win32':
        answer = ctypes.windll.user32.MessageBoxW(None, text + '\n\n是否安装？', '安装建模依赖', 0x24)
        if answer == 6:
            subprocess.Popen([str(console_python()), '-B', str(Path(__file__).with_name('runtime.py')), 'gui',
                              '--python', str(console_python()), '--launch-files', *map(str, files)],
                             cwd=ROOT, env=process_env(console_python()), creationflags=subprocess.CREATE_NEW_CONSOLE)
            return True
    elif sys.platform == 'darwin':
        from .mac import install_prompt
        install_prompt(text)
    else:
        message(missing_message(version_problems(found, CATALOG['gui_bounds'])))
    return False


def main():
    started = perf_counter()
    os.environ['QT_API'] = 'pyside6'
    if sys.platform == 'darwin':
        os.environ.pop('PYTORCH_ENABLE_MPS_FALLBACK', None)
    logs = work_dir() / "log"
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / "launch.log"
    stream = log_path.open("a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = stream
    print(f"\n--- {datetime.now().isoformat(timespec='seconds')} ---")
    print("Python:", sys.executable, sys.version)

    # Report unhandled Python failures at the GUI entry, including Qt callbacks.
    def report(kind, error, trace):
        traceback.print_exception(kind, error, trace)
        stream.flush()
        message(f"{kind.__name__}: {error}\n\n详细日志：{log_path}")

    sys.excepthook = report
    if not supported(sys.version_info, struct.calcsize('P') * 8, platform.python_implementation(), bool(sysconfig.get_config_var('Py_GIL_DISABLED'))):
        hint = "按住 Shift 双击启动器可重新选择 Python。" if sys.platform == "win32" else "请使用自己的 Python 3.12 环境重新启动。"
        message("VBuild 支持 64 位常规 CPython 3.11–3.13，推荐 3.12；M 系列请使用 arm64 Python。\n" + hint)
        return 1
    if sys.platform == 'darwin' and tuple(map(int, platform.mac_ver()[0].split('.')[:2])) < (14, 0):
        message('Mac 轻量版需要 macOS 14 或更新版本。')
        return 1
    found, missing = versions(GUI)
    print("GUI:", found)
    problems = version_problems(found, CATALOG['gui_bounds'])
    print(f'Startup: dependency check {perf_counter() - started:.3f}s', flush=True)
    if problems:
        offer_gui_install(found, sys.argv[1:])
        return 1
    qt_started = perf_counter()
    from .app import launch
    print(f'Startup: Qt entry import {perf_counter() - qt_started:.3f}s', flush=True)
    launch(paths=[Path(value) for value in sys.argv[1:]])
    return 0
