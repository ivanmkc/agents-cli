"""Make tools/evolve importable for the retrieval-evolution tests."""

import sys
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parents[3] / "tools"
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))
