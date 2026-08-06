"""Reference tools that lift is measured against.

``DEFAULT_GREP_PATH`` is a cheap programmatic *proxy* for what agents do
today without a dedicated search tool: grep for terms, then read whole
files into context. The authoritative "default" is a real agent using
its native tools — measured by ``agent_lift.py`` — but that costs LLM
calls per task, so the inner evolution loop compares against this proxy
instead.
"""

from pathlib import Path

DEFAULT_GREP_PATH = str(Path(__file__).resolve().parent / "default_grep.py")
