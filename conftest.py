"""Root conftest: anchors pytest rootdir discovery and adds tools/ to sys.path."""

import sys
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent / "tools"
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))
