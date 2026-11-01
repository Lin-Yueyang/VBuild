"""The command-line path does not import Qt or VTK."""

import argparse
import json
from pathlib import Path

import numpy as np

from .poscar import PoscarDocument


def main() -> None:
    parser = argparse.ArgumentParser(description="VBuild：静态 VASP 结构编辑器")
    commands = parser.add_subparsers(dest="command")
    gui = commands.add_parser("gui", help="打开静态视窗")
    gui.add_argument("input", nargs="?", type=Path)
    inspect = commands.add_parser("inspect", help="检查 POSCAR 元数据")
    inspect.add_argument("input", type=Path)
    copy = commands.add_parser("copy", help="原样另存，不覆盖已有文件")
    copy.add_argument("input", type=Path)
    copy.add_argument("output", type=Path)
    move = commands.add_parser("move", help="按 Å 移动指定原子，保留约束和温度")
    move.add_argument("input", type=Path)
    move.add_argument("output", type=Path)
    move.add_argument("--atoms", nargs="+", type=int, required=True, help="从 1 开始的原子编号")
    move.add_argument("--delta", nargs=3, type=float, required=True, metavar=("X", "Y", "Z"))
    thermal = move.add_mutually_exclusive_group()
    thermal.add_argument("--temperature", type=float, help="重新赋速的温度，单位 K")
    thermal.add_argument("--potim", type=float, help="原 Direct 速度的时间步，单位 fs")
    move.add_argument("--seed", type=int, help="速度随机种子，可复现导出")
    args = parser.parse_args()

    if args.command in (None, "gui"):
        from .app import launch
        launch(None if args.command is None else args.input)
        return
    document = PoscarDocument.read(args.input)
    if args.command == "inspect":
        print(json.dumps(document.summary(), ensure_ascii=False, indent=2))
    elif args.command == "copy":
        document.write(args.output, regenerate=False)
        print(args.output)
    elif args.command == "move":
        document.ensure_editable()
        if args.temperature is not None:
            document.structure.set_temperature(args.temperature)
        elif args.potim is not None:
            document.structure.set_temperature(document.input_temperature(args.potim))
        document.structure.translate([index - 1 for index in args.atoms], np.array(args.delta))
        document.write(args.output, seed=args.seed)
        print(args.output)
