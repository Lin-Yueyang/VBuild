"""Python checks and explicit model/dependency downloads for the selected Python."""

import argparse
from datetime import datetime
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import struct
import subprocess
import sys
import urllib.request
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vbuild.paths import ROOT, work_dir, models_dir, install_cache
from vbuild.devices import install_device, check_device
CATALOG = json.loads(Path(__file__).with_suffix('.json').read_text(encoding='utf-8'))
GUI = ('numpy', 'ase', 'scipy', 'PySide6', 'pyvista', 'pyvistaqt', 'vtk', 'spglib', 'matplotlib')
NO_WINDOW = 0x08000000 if sys.platform == 'win32' else 0


def console_python(path):
    path = Path(path)
    return path.with_name('python.exe') if path.name.lower() == 'pythonw.exe' else path


def supported(version, bits=64, implementation='CPython', free_threaded=False, machine=None, system=None):
    system = sys.platform if system is None else system
    architectures = ('arm64', 'x86_64') if system == 'darwin' else ('amd64', 'x86_64')
    return (tuple(CATALOG['python_min']) <= tuple(version[:2]) < tuple(CATALOG['python_max'])
            and bits == 64 and implementation == 'CPython' and not free_threaded
            and (platform.machine() if machine is None else machine).lower() in architectures)


def info():
    packages = {}
    for name in (*GUI, 'torch', 'mace-torch', 'nep-adapters', 'deepmd-kit', 'vesin', 'vesin-torch', 'requests'):
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            pass
    bits = struct.calcsize('P') * 8
    free = bool(sysconfig_value('Py_GIL_DISABLED'))
    deepmd_cpp = ('deepmd-kit' in packages and Path(metadata.distribution('deepmd-kit').locate_file(
        'deepmd/lib/deepmd_op_pt.dll' if sys.platform == 'win32' else 'deepmd/lib/libdeepmd_op_pt.so')).is_file())
    return dict(python=str(console_python(sys.executable)), base=sys.base_prefix, prefix=sys.prefix, version=list(sys.version_info[:3]),
                system=sys.platform, machine=platform.machine().lower(), macos=platform.mac_ver()[0],
                bits=bits, implementation=platform.python_implementation(), free_threaded=free,
                supported=supported(sys.version_info, bits, platform.python_implementation(), free), packages=packages, deepmd_cpp=deepmd_cpp,
                gui=not version_problems(packages, CATALOG['gui_bounds']))


def version_problems(packages, bounds):
    problems = []
    for name, (minimum, maximum) in bounds.items():
        match = re.match(r'(\d+)\.(\d+)(?:\.(\d+))?', packages.get(name, ''))
        release = tuple(int(value or 0) for value in match.groups()) if match else ()
        if not tuple(minimum) <= release < tuple(maximum):
            problems.append(name + ': ' + packages.get(name, '未安装'))
    return problems


def sysconfig_value(name):
    import sysconfig
    return sysconfig.get_config_var(name)


def process_env(python):
    env = os.environ.copy()
    home = str(Path(python).parent)
    folders = (home, str(Path(home) / 'Scripts'), str(Path(home) / 'Library/bin')) if sys.platform == 'win32' else (home,)
    env['PATH'] = os.pathsep.join((*folders, env.get('PATH', '')))
    env['PYTHONUTF8'], env['QT_API'] = '1', 'pyside6'
    env.pop('PYTHONPATH', None)
    env.pop('PYTHONHOME', None)
    # Never enable an implicit MPS -> CPU fallback in the selected interpreter.
    if sys.platform == 'darwin':
        env.pop('PYTORCH_ENABLE_MPS_FALLBACK', None)
    return env


def probe(python):
    python = console_python(python)
    result = subprocess.run([str(python), '-B', str(Path(__file__).resolve()), 'probe'],
        capture_output=True, text=True, encoding='utf-8', timeout=15, env=process_env(python), creationflags=NO_WINDOW)
    if result.returncode:
        raise ValueError(result.stderr.strip() or '无法运行所选 Python。')
    return json.loads(result.stdout)


def state(root=ROOT):
    path = work_dir(root) / 'runtime.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'engines': {}}


def save_state(value, root=ROOT):
    path = work_dir(root) / 'runtime.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def engine_python(engine, device, root=ROOT):
    return state(root)['engines'].get(engine, {}).get(device)


def select_python(python, root=ROOT):
    return remember_python(probe(python), root)


def remember_python(details, root=ROOT):
    if not details['supported']:
        raise ValueError('需要 Python 3.11–3.13（64 位）。')
    if not details['gui']:
        raise ValueError('需要 Python 3.11–3.13（64 位）及建模依赖：' + '、'.join(version_problems(details['packages'], CATALOG['gui_bounds'])))
    path = work_dir(root) / 'python.txt'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(details['python'] + '\n', encoding='utf-8')
    save_state({'engines': {}}, root)
    return details


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def download(model, root=ROOT, progress=print):
    # URLs and expected bytes/digests are shipped in the reviewed catalog, not supplied by a server.
    # Import works both as an installed module and as the stdlib-only bootstrap script.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from vbuild.mlip import MODELS
    item = CATALOG['models'][model]
    target = models_dir(root) / MODELS[model].file
    if target.exists():
        if target.stat().st_size != item['bytes'] or digest(target) != item['sha256']:
            raise ValueError(f'已有模型与官方摘要不同，未覆盖：{target}。请先手动选择其他模型路径或移动此文件。')
        progress('模型已存在，SHA256 核对通过。')
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + '.' + uuid.uuid4().hex[:8] + '.part')
    request = urllib.request.Request(item['url'], headers={'User-Agent': 'VBuild/Windows'})
    checksum, count, report_at = hashlib.sha256(), 0, 0
    with urllib.request.urlopen(request, timeout=45) as response, partial.open('xb') as stream:
        for chunk in iter(lambda: response.read(1024 * 1024), b''):
            stream.write(chunk)
            checksum.update(chunk)
            count += len(chunk)
            if count >= report_at:
                progress(f'下载模型 {count / 1048576:.1f} / {item["bytes"] / 1048576:.1f} MiB')
                report_at = count + 5 * 1048576
    if count != item['bytes'] or checksum.hexdigest() != item['sha256']:
        raise ValueError(f'模型大小或 SHA256 不符，未启用下载文件：{partial}')
    partial.rename(target)
    return target


def run(command, root=ROOT):
    print(subprocess.list2cmdline(list(map(str, command))), flush=True)
    with subprocess.Popen(list(map(str, command)), cwd=root, env=process_env(command[0]), creationflags=NO_WINDOW,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace') as process:
        for line in process.stdout:
            print(line, end='', flush=True)
        code = process.wait()
    if code:
        raise subprocess.CalledProcessError(code, command)


def pip_command(python, root=ROOT, index='https://pypi.org/simple'):
    prefix = Path(probe(python)['prefix'])
    # Preserve long-path support when the user chooses an existing Windows venv.
    options = ['--prefix', '\\\\?\\' + str(prefix)] if sys.platform == 'win32' and (prefix / 'pyvenv.cfg').is_file() else []
    return [python, '-B', '-m', 'pip', '--isolated', '--disable-pip-version-check', 'install',
         *options,
         '--cache-dir', str(install_cache(root).resolve() / 'pip'),
         '--index-url', index, '--prefer-binary', '--only-binary=numpy,scipy,torch,nep-adapters,matscipy,h5py,orjson,pandas',
         '--progress-bar', 'off']


def pip_install(python, packages, root=ROOT, index='https://pypi.org/simple'):
    run([*pip_command(python, root, index), *packages], root)


def gui_plan(packages):
    problems = version_problems(packages, CATALOG['gui_bounds'])
    names = {text.split(':', 1)[0] for text in problems}
    return [requirement for requirement in CATALOG['gui'] if requirement.split('==', 1)[0] in names]


def install_gui(python, root=ROOT):
    details = probe(python)
    if not details['supported']:
        raise ValueError('请选择 CPython 3.11–3.13 的 64 位常规版本。')
    packages = gui_plan(details['packages'])
    if packages:
        pip_install(console_python(python), packages, root)
    details = probe(python)
    if not details['gui']:
        raise ValueError('建模依赖安装后检查未通过：' + '、'.join(version_problems(details['packages'], CATALOG['gui_bounds'])))
    return details


def gpu_driver():
    binary = shutil.which('nvidia-smi')
    if binary is None:
        raise ValueError('未找到 NVIDIA 驱动工具 nvidia-smi；请先安装驱动，或明确选择 CPU。')
    result = subprocess.run([binary, '--query-gpu=name,driver_version', '--format=csv,noheader'],
        capture_output=True, text=True, encoding='utf-8', timeout=15, creationflags=NO_WINDOW)
    if result.returncode or not result.stdout.strip():
        raise RuntimeError('未检测到可用的 NVIDIA 驱动；请先安装驱动，或明确选择 CPU。' + result.stderr.strip())
    return result.stdout.strip()


def compute_requirements(details, engines, device):
    from packaging.requirements import Requirement
    result = {}
    for engine in engines:
        for text in CATALOG[engine]:
            requirement = Requirement(text)
            name = requirement.name
            compatible = (not version_problems(details['packages'], {name: CATALOG['engine_bounds'][name]})
                          if name in CATALOG['engine_bounds'] else
                          name in details['packages'] and details['packages'][name] in requirement.specifier)
            extras = '[' + ','.join(sorted(requirement.extras)) + ']' if requirement.extras else ''
            result[name] = name + extras + '==' + details['packages'][name] if compatible else text
    if any(engine in ('mace', 'deepmd') for engine in engines):
        current = details['packages'].get('torch', '')
        compatible = not version_problems(details['packages'], {'torch': CATALOG['engine_bounds']['torch']})
        if compatible and (device != 'cuda' or '+cu' in current):
            result['torch'] = 'torch==' + current
        else:
            suffix = '+' + CATALOG['torch_indexes'][device].rsplit('/', 1)[-1] if device == 'cuda' else '+cpu' if details.get('system') != 'darwin' else ''
            result['torch'] = 'torch==' + CATALOG['torch'] + suffix
    return list(result.values())


def plan_models(python, models, device, root=ROOT):
    from vbuild import dependencies
    from vbuild.mlip import MODELS
    target = console_python(python)
    details = probe(target)
    if not details['supported']:
        raise ValueError('请先选择 CPython 3.11–3.13 的 64 位常规版本。')
    if not models:
        raise ValueError('请勾选模型。')
    if details.get('system') == 'darwin' and details.get('machine') != 'arm64' and any(MODELS[name].engine == 'mace' for name in models):
        raise ValueError('此 MACE 版本需要 Apple Silicon 原生 arm64 Python；Intel Mac 可使用建模和 NEP CPU。')
    if device == 'cuda':
        print('NVIDIA 驱动：' + gpu_driver(), flush=True)
    engines = tuple(dict.fromkeys(MODELS[name].engine for name in models))
    for engine in engines:
        check_device(engine, install_device(engine, device))
    requirements = compute_requirements(details, engines, device)
    index = CATALOG['torch_indexes'][device] if device in ('cpu', 'cuda') and any(engine in ('mace', 'deepmd') for engine in engines) else None
    plan = dependencies.prepare(target, requirements, root, index)
    plan['models'] = models
    plan['notices'] = (['DPA4C 将使用纯 PyTorch；现有 DeePMD 编译扩展会移到备份，可能影响其他依赖该扩展的 DeePMD 模型。']
                       if 'deepmd' in engines and details.get('deepmd_cpp', False) else [])
    return dict(models=models, device=device, jobs=[plan])


def install_models(python, models, device, root=ROOT, *, plan=None, approved=False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from vbuild.mlip import MODELS
    from vbuild import dependencies
    plan = plan_models(python, models, device, root) if plan is None else plan
    if plan['models'] != models or plan['device'] != device or console_python(plan['jobs'][0]['python']) != console_python(python):
        raise ValueError('Python 或模型选择已变化，请重新检查依赖。')
    changes = any(item['current'] is not None for job in plan['jobs'] for item in job['changes'])
    if (changes or any(job.get('notices') for job in plan['jobs'])) and not approved:
        raise PermissionError('需要先确认依赖版本变更。')
    result = []
    for job in plan['jobs']:
        dependencies.apply(job, root, approved)
        if any(MODELS[name].engine == 'deepmd' for name in job['models']):
            run([job['python'], '-B', str(Path(__file__).with_name('dpa_setup.py')), '--shared'], root)
        for model in job['models']:
            download(model, root)
            result.append(bind_engine(job['python'], model, install_device(MODELS[model].engine, device), root))
            if MODELS[model].engine == 'deepmd' and device == 'cuda':
                bind_engine(job['python'], model, 'cpu', root)
    return result


def bind_engine(target, model, device, root=ROOT):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from vbuild.mlip import MODELS
    check_device(MODELS[model].engine, device)
    path = models_dir(root) / MODELS[model].file
    details = probe(target)
    if not details['supported']:
        raise ValueError('计算 Python 需要 3.11–3.13、64 位常规 CPython。')
    required = {'nep': ('numpy', 'ase', 'scipy', 'nep-adapters'),
                'mace': ('numpy', 'ase', 'scipy', 'mace-torch', 'torch'),
                'deepmd': ('numpy', 'ase', 'scipy', 'deepmd-kit', 'torch')}[MODELS[model].engine]
    problems = version_problems(details['packages'], {name: CATALOG['engine_bounds'][name] for name in required})
    if problems:
        raise ValueError('计算依赖缺失或版本不兼容：' + ', '.join(problems))
    # A real model/force calculation must pass before publishing an environment as ready.
    run([target, '-B', str(Path(__file__).with_name('engine.py')), 'check', model, str(path), device], root)
    value = state(root)
    value['engines'].setdefault(MODELS[model].engine, {})[device] = str(console_python(target))
    save_state(value, root)
    return dict(python=str(target), model=model, device=device, path=str(path))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('probe', 'gui', 'plan', 'install', 'select', 'bind'))
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--python', default=str(console_python(sys.executable)))
    parser.add_argument('--model', choices=tuple(CATALOG['models']), default='nep89')
    parser.add_argument('--models', choices=tuple(CATALOG['models']), nargs='+')
    parser.add_argument('--device', choices=('cpu', 'cuda', 'mps'), default='cpu')
    parser.add_argument('--launch-files', nargs='*', default=None)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--approve-changes', action='store_true')
    args = parser.parse_args()
    args.root = args.root.resolve()
    if args.action == 'probe':
        result = info() if console_python(args.python) == console_python(sys.executable) else probe(args.python)
        print(json.dumps(result, ensure_ascii=False))
        return
    logs = work_dir(args.root) / 'log'
    logs.mkdir(parents=True, exist_ok=True)
    print('VBuild setup · ' + datetime.now().isoformat(timespec='seconds'), flush=True)
    if args.action == 'gui':
        try:
            result = install_gui(args.python, args.root)
        except (OSError, ValueError, subprocess.CalledProcessError) as error:
            import traceback
            trace = traceback.format_exc()
            (logs / 'gui-install-error.log').write_text(trace, encoding='utf-8')
            print(trace, flush=True)
            if args.launch_files is not None:
                from vbuild.launch import message
                message('建模依赖安装失败：' + str(error) + '\n日志：' + str(logs / 'gui-install-error.log'))
            return 1
        if args.launch_files is not None:
            target = console_python(args.python)
            if sys.platform == 'win32' and target.with_name('pythonw.exe').is_file():
                target = target.with_name('pythonw.exe')
            subprocess.Popen([str(target), '-B', str(args.root / 'gui.pyw'), *args.launch_files],
                             cwd=args.root, env=process_env(args.python), creationflags=NO_WINDOW)
    elif args.action == 'plan':
        plan = plan_models(args.python, args.models or [args.model], args.device, args.root)
        folder = install_cache(args.root) / 'plans'
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / ('install-' + uuid.uuid4().hex[:8] + '.json')
        path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
        result = dict(plan=str(path))
    elif args.action == 'install':
        plan = None if args.plan is None else json.loads(args.plan.read_text(encoding='utf-8'))
        result = install_models(args.python, args.models or [args.model], args.device, args.root,
                                plan=plan, approved=args.approve_changes)
    elif args.action == 'bind':
        result = bind_engine(args.python, args.model, args.device, args.root)
    else:
        result = select_python(args.python, args.root)
    print('VBUILD_RESULT ' + json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    sys.exit(main())
