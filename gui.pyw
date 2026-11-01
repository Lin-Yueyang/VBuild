"""Console-free checkout entry; all application code lives in src/vbuild."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from vbuild.launch import main

if __name__ == "__main__":
    sys.exit(main())
