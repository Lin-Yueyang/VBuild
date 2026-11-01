"""Read the maintained GitHub branch and download a pinned, verified update."""

import argparse
import json
from pathlib import Path
import re
from shutil import copyfile
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid
from zipfile import BadZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vbuild.paths import ROOT, work_dir
from vbuild.update_files import unpack, validate_manifest

REPOSITORY = 'Lin-Yueyang/VBuild'
API = 'https://api.github.com/repos/' + REPOSITORY
HOSTS = {'api.github.com', 'github.com', 'codeload.github.com'}


class GitHubRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        target = urlsplit(newurl)
        if target.scheme != 'https' or target.hostname not in HOSTS:
            raise ValueError('GitHub 下载重定向到了未授权的地址。')
        redirected = super().redirect_request(request, response, code, message, headers, newurl)
        return redirected


def open_github(url, *, raw=False):
    target = urlsplit(url)
    if target.scheme != 'https' or target.hostname != 'api.github.com' or not target.path.startswith('/repos/' + REPOSITORY + '/'):
        raise ValueError('更新来源必须是指定的 GitHub 仓库。')
    headers = {'Accept': 'application/vnd.github.raw+json' if raw else 'application/vnd.github+json',
               'User-Agent': 'VBuild-updater', 'X-GitHub-Api-Version': '2026-03-10'}
    try:
        return build_opener(GitHubRedirect()).open(Request(url, headers=headers), timeout=30)
    except HTTPError as error:
        messages = {401: '无法读取更新仓库，请确认仓库已公开。',
                    403: 'GitHub 暂时拒绝访问，请稍后重试。',
                    404: '未找到更新文件，请确认仓库已公开并已上传更新。'}
        raise ValueError(messages.get(error.code, 'GitHub 请求失败，状态码 ' + str(error.code))) from None
    except URLError:
        raise ValueError('无法连接 GitHub，请检查网络后重试。') from None


def check():
    with open_github(API + '/git/ref/heads/main') as response:
        commit = json.load(response)['object']['sha']
    if re.fullmatch('[0-9a-f]{40}', commit) is None:
        raise ValueError('GitHub 提交编号无效。')
    with open_github(API + '/contents/manifest.json?ref=' + commit, raw=True) as response:
        manifest = json.load(response)
    validate_manifest(manifest)
    return dict(version=manifest['version'], commit=commit, manifest=manifest)


def download(info, root=ROOT, progress=lambda current, total: None):
    validate_manifest(info['manifest'])
    if info['version'] != info['manifest']['version'] or re.fullmatch('[0-9a-f]{40}', info['commit']) is None:
        raise ValueError('更新信息无效，请重新检查。')
    folder = work_dir(root) / 'updates' / (info['version'] + '_' + uuid.uuid4().hex[:8])
    folder.mkdir(parents=True)
    archive, count = folder / 'download.part', 0
    with open_github(API + '/zipball/' + info['commit']) as response, archive.open('xb') as stream:
        total = int(response.headers.get('Content-Length', '0'))
        while data := response.read(256 * 1024):
            stream.write(data)
            count += len(data)
            progress(count, total)
    stage = unpack(archive, folder / 'stage', info['manifest'])
    final = folder / ('VBuild_' + info['version'] + '.zip')
    archive.rename(final)
    return dict(version=info['version'], stage=str(stage), archive=str(final))


def prepare_installer(stage, root=ROOT, pid=0):
    import tomllib
    from vbuild.runtime import console_python
    from vbuild.update_files import staged_files, version
    root, stage = Path(root).resolve(), Path(stage).resolve()
    newest, _files = staged_files(stage)
    installed = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    if version(newest['version']) <= version(installed['version']):
        raise ValueError('当前版本已不低于下载的版本。')
    current = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    if current != installed['version']:
        raise ValueError('当前目录不是完整分享版本，请先恢复程序文件。')
    helper = stage.parent / ('helper_' + uuid.uuid4().hex[:8])
    helper.mkdir()
    for name in ('update_apply.py', 'update_files.py'):
        copyfile(Path(__file__).with_name(name), helper / name)
    python = console_python(sys.executable)
    if python.with_name('pythonw.exe').is_file():
        python = python.with_name('pythonw.exe')
    return [str(python), '-B', str(helper / 'update_apply.py'), '--root', str(root), '--stage', str(stage),
            '--wait', str(pid), '--restart']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('check', 'download'))
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    request = json.loads(sys.stdin.readline())
    try:
        if args.action == 'check':
            result = check()
        else:
            def progress(current, total):
                print('VBUILD_PROGRESS ' + json.dumps(dict(current=current, total=total)), flush=True)
            result = download(request['info'], root=args.root, progress=progress)
        print('VBUILD_RESULT ' + json.dumps(result), flush=True)
    except (OSError, ValueError, KeyError, BadZipFile) as error:
        print('VBUILD_ERROR ' + json.dumps(dict(message=str(error))), flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
