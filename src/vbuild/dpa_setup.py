"""Explicitly select DPA4C's pure PyTorch path and preserve optional binaries."""

from importlib.metadata import distribution
from pathlib import Path
import shutil
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vbuild.paths import install_cache


def prepare(allow_shared=False):
    prefix = Path(sys.prefix).resolve()
    if prefix == Path(sys.base_prefix).resolve() and not allow_shared:
        raise ValueError('配置主 Python 的 DeePMD 需要明确指定 --shared。')
    binary = Path(distribution('deepmd-kit').locate_file('deepmd/lib/deepmd_op_pt.dll' if sys.platform == 'win32' else 'deepmd/lib/libdeepmd_op_pt.so'))
    if binary.is_file():
        saved = install_cache() / 'disabled-ops'
        saved.mkdir(parents=True, exist_ok=True)
        shutil.move(str(binary), str(saved / (binary.name + '.' + uuid.uuid4().hex[:8])))
    print('DPA4C：ASE 纯 PyTorch 模式；可选编译扩展已保留。', flush=True)


if __name__ == '__main__':
    prepare('--shared' in sys.argv)
