"""Check the chosen Python; installation requires an explicit user action."""

import json
import platform
import subprocess
import sys

from . import runtime
from .paths import ROOT, work_dir


def install_prompt(text):
    script = '''on run argv
        set answer to display dialog (item 1 of argv) with title "VBuild" buttons {"取消", "安装依赖"} default button "安装依赖" cancel button "取消"
        return button returned of answer
    end run'''
    result = subprocess.run(['/usr/bin/osascript', '-e', script, text], capture_output=True, text=True)
    if result.returncode == 0:
        subprocess.run(['/usr/bin/open', str(ROOT / 'launch/install.command')], check=True)


def main():
    # This bootstrap deliberately imports neither Qt nor scientific packages.
    from .launch import message
    details = runtime.info()
    if '--check' in sys.argv:
        print(json.dumps(details, ensure_ascii=False))
        return int(not details['supported'] or not details['gui'])
    if not details['supported'] or tuple(map(int, platform.mac_ver()[0].split('.')[:2])) < (14, 0):
        message('需要 macOS 14+ 和 Python 3.11–3.13（推荐 3.12）。M 系列请选择原生 arm64 Python。\n运行 launch/start.command --python 可重新选择。')
        return 1
    if '--install' in sys.argv:
        # Only the separately opened installation command reaches this path.
        sys.argv.remove('--install')
        print('正在为所选 Python 安装建模依赖；不安装 MLIP、不新建环境。', flush=True)
        details = runtime.install_gui(sys.executable)
    if not details['gui']:
        install_prompt('需要安装建模依赖：\n' + '、'.join(runtime.gui_plan(details['packages']))
                       + '\nQt/VTK 体积较大。安装完成后自动启动。\nPython：' + sys.executable)
        return 1
    work = work_dir()
    work.mkdir(parents=True, exist_ok=True)
    (work / 'python.txt').write_text(sys.executable + '\n', encoding='utf-8')
    from .launch import main as launch
    return launch()
