"""Resolve first, approve changes to installed libraries, then install that exact plan."""

import json
import re
import subprocess
import uuid

from packaging.requirements import Requirement


def canonical(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def inventory(python):
    from .runtime import NO_WINDOW, process_env
    result = subprocess.run([str(python), '-B', '-m', 'pip', 'inspect'], capture_output=True,
                            text=True, encoding='utf-8', check=True,
                            env=process_env(python), creationflags=NO_WINDOW)
    return json.loads(result.stdout)


def versions(snapshot):
    return {canonical(item['metadata']['name']): item['metadata']['version'] for item in snapshot['installed']}


def changes(snapshot, report):
    old = versions(snapshot)
    return [dict(name=item['metadata']['name'], current=old.get(canonical(item['metadata']['name'])),
                 version=item['metadata']['version'], url=item['download_info']['url']) for item in report['install']]


def conflicts(snapshot, report):
    changed = {canonical(item['metadata']['name']): item['metadata']['version'] for item in report['install']}
    affected = []
    for item in snapshot['installed']:
        meta = item['metadata']
        if canonical(meta['name']) in changed:
            continue
        for text in meta.get('requires_dist', []):
            requirement = Requirement(text)
            name = canonical(requirement.name)
            enabled = requirement.marker is None or requirement.marker.evaluate({**snapshot['environment'], 'extra': ''})
            if enabled and name in changed and changed[name] not in requirement.specifier:
                affected.append(f'{meta["name"]} {meta["version"]} 需要 {requirement.name}{requirement.specifier}')
    return sorted(set(affected))


def prepare(python, requirements, root, torch_index=None):
    from .runtime import pip_command, run
    from .paths import install_cache
    snapshot = inventory(python)
    folder = install_cache(root) / 'plans' / uuid.uuid4().hex[:8]
    folder.mkdir(parents=True)
    command = [*pip_command(python, root), '--dry-run', '--report', str(folder / 'resolver.json')]
    if torch_index is not None:
        command.extend(['--extra-index-url', torch_index])
    run([*command, *requirements], root)
    report = json.loads((folder / 'resolver.json').read_text(encoding='utf-8'))
    return dict(python=str(python), snapshot=versions(snapshot), changes=changes(snapshot, report),
                conflicts=conflicts(snapshot, report), requirements=requirements)


def apply(plan, root, approved=False):
    from .runtime import pip_command, run
    if any(item['current'] is not None for item in plan['changes']) and not approved:
        raise PermissionError('已安装库的版本变更尚未确认。')
    if versions(inventory(plan['python'])) != plan['snapshot']:
        raise ValueError('Python 环境已变化，请重新检查依赖。')
    if plan['changes']:
        run([*pip_command(plan['python'], root), '--no-deps', *(item['url'] for item in plan['changes'])], root)


def summary(plans):
    rows = []
    for plan in plans:
        rows.append('Python：' + plan['python'])
        for item in plan['changes']:
            if item['current'] is not None:
                rows.append(f'{item["name"]}：{item["current"]} → {item["version"]}')
        rows.extend(plan['conflicts'])
        rows.extend(plan.get('notices', []))
    return '\n'.join(rows)
