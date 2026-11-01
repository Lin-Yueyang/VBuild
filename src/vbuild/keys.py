"""Shared key map; Qt maps Ctrl to Command on macOS."""

import json
from pathlib import Path
import sys


def bindings(overrides=None):
    keys = json.loads(Path(__file__).with_suffix('.json').read_text(encoding='utf-8'))
    mac = keys.pop('mac')
    result = keys | mac if sys.platform == 'darwin' else keys
    return result | {name: value for name, value in (overrides or {}).items() if name in result}
