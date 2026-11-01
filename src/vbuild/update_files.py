"""Manifest-based update staging; keep user files outside the program payload."""

import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
from shutil import move
import tomllib
import uuid
from zipfile import ZIP_DEFLATED, ZipFile

TOP = {'.gitattributes', '.gitignore', 'README.md', 'VBuild.exe', 'gui.pyw', 'run.py', 'pyproject.toml'}


def version(value):
    if re.fullmatch(r'\d+\.\d+\.\d+', value) is None:
        raise ValueError('更新版本号无效。')
    return tuple(map(int, value.split('.')))


def program_path(name):
    path = PurePosixPath(name)
    if ('\\' in name or PureWindowsPath(name).drive or path.is_absolute()
            or any(part in ('', '.', '..') for part in name.split('/'))):
        raise ValueError('更新包路径无效。')
    allowed = (name in TOP or name == 'MLIP/README.md'
               or name in ('tools/check.py', 'tools/launch.cs', 'tools/launch.ps1')
               or path.parts[0] == 'docs' and path.suffix == '.md'
               or name.startswith('src/vbuild/') and path.suffix in ('.py', '.json', '.svg', '.png', '.ico'))
    if not allowed or any(part.lower() in ('.git', 'work', 'data', '__pycache__') for part in path.parts):
        raise ValueError('更新包含非程序文件。')
    return name


def validate_manifest(manifest):
    version(manifest['version'])
    names = []
    for item in manifest['files']:
        names.append(program_path(item['path']))
        if item['bytes'] < 0 or re.fullmatch('[0-9a-f]{64}', item['sha256']) is None:
            raise ValueError('更新清单的文件大小或哈希无效。')
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError('更新清单含重复路径。')
    if not {'pyproject.toml', 'VBuild.exe', 'gui.pyw', 'src/vbuild/app.py'} <= set(names):
        raise ValueError('更新包缺少必要程序文件。')


def unpack(archive, stage, expected):
    validate_manifest(expected)
    stage = Path(stage)
    with ZipFile(archive) as bundle:
        manifests = [name for name in bundle.namelist() if name.count('/') == 1 and name.endswith('/manifest.json')]
        if len(manifests) != 1:
            raise ValueError('更新包缺少唯一的文件清单。')
        prefix = manifests[0].removesuffix('manifest.json')
        raw = bundle.read(manifests[0])
        if json.loads(raw) != expected:
            raise ValueError('更新包与检查到的版本不一致。')
        contents = {}
        for item in expected['files']:
            info = bundle.getinfo(prefix + item['path'])
            if info.file_size != item['bytes'] or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('更新文件大小或类型不一致：' + item['path'])
            data = bundle.read(info)
            if hashlib.sha256(data).hexdigest() != item['sha256']:
                raise ValueError('更新文件校验失败：' + item['path'])
            contents[item['path']] = data
        found = tomllib.loads(contents['pyproject.toml'].decode('utf-8'))['project']['version']
        if found != expected['version']:
            raise ValueError('程序和更新清单版本不一致。')
        contents['manifest.json'] = raw
    stage.mkdir(parents=True)
    for name, data in contents.items():
        path = stage / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return stage


def destination(root, name):
    if name != 'manifest.json':
        program_path(name)
    path = root / name
    if (not path.resolve().is_relative_to(root) or path.is_symlink()
            or any(parent.is_symlink() for parent in path.parents if parent.is_relative_to(root))):
        raise ValueError('程序路径不能经过符号链接。')
    if path.exists() and not path.is_file():
        raise ValueError('程序文件位置已被文件夹占用：' + name)
    return path


def staged_files(stage):
    manifest = json.loads((stage / 'manifest.json').read_text(encoding='utf-8'))
    validate_manifest(manifest)
    files = {}
    for item in manifest['files']:
        data = destination(stage, item['path']).read_bytes()
        if len(data) != item['bytes'] or hashlib.sha256(data).hexdigest() != item['sha256']:
            raise ValueError('待安装文件已改变：' + item['path'])
        files[item['path']] = data
    files['manifest.json'] = (stage / 'manifest.json').read_bytes()
    if tomllib.loads(files['pyproject.toml'].decode())['project']['version'] != manifest['version']:
        raise ValueError('待安装版本不一致。')
    return manifest, files


def install(stage, root):
    stage, root = Path(stage).resolve(), Path(root).resolve()
    newest, files = staged_files(stage)
    previous = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    validate_manifest(previous)
    if version(newest['version']) <= version(previous['version']):
        raise ValueError('当前版本已不低于下载的版本，请重新检查更新。')
    owned = {item['path'] for item in previous['files']} | {'manifest.json'}
    paths = {name: destination(root, name) for name in owned | files.keys()}
    old = {name: path.read_bytes() for name, path in paths.items() if path.is_file()}
    transaction = stage.parent / ('install_' + uuid.uuid4().hex[:8])
    transaction.mkdir()
    backup = transaction / 'before.zip'
    with ZipFile(backup, 'x', ZIP_DEFLATED) as archive:
        for name, data in old.items():
            archive.writestr(name, data)
    touched = []
    try:
        ordered = sorted(files, key=lambda name: (name == 'manifest.json', name == 'VBuild.exe', name))
        for name in ordered:
            touched.append(name)
            paths[name].parent.mkdir(parents=True, exist_ok=True)
            paths[name].write_bytes(files[name])
        for name in sorted(owned - files.keys()):
            if paths[name].is_file():
                touched.append(name)
                removed = transaction / 'removed' / name
                removed.parent.mkdir(parents=True, exist_ok=True)
                move(str(paths[name]), str(removed))
    except OSError:
        for name in reversed(touched):
            if name in old:
                paths[name].parent.mkdir(parents=True, exist_ok=True)
                paths[name].write_bytes(old[name])
            elif paths[name].is_file():
                failed = transaction / 'failed' / name
                failed.parent.mkdir(parents=True, exist_ok=True)
                move(str(paths[name]), str(failed))
        raise
    result = dict(version=newest['version'], previous=previous['version'], backup=str(backup))
    (transaction / 'result.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return result
