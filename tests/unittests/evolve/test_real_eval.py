"""Tests for building a replayable eval set from mined episodes.

Real transcripts show two dominant retrieval workloads:

* **sdk-symbol** — reverse-engineering an installed SDK: ``grep "class
  LlmAgent" .../site-packages/google/adk/...``. Replayable against a
  checkout of the SDK; ground truth is the AST-located definition span.
* **project-file** — orienting inside the scaffolded project: reads of
  ``app/agent.py``, ``pyproject.toml``. Replayable against the scaffold
  template; ground truth is file-level.
"""

from pathlib import Path

import pytest
from evolve.retrieval.real_eval import (
    build_cases,
    extract_symbols,
    locate_definition,
)


@pytest.fixture()
def sdk_corpus(tmp_path):
    root = tmp_path / "sdk"
    (root / "agents").mkdir(parents=True)
    (root / "agents" / "llm_agent.py").write_text(
        "class BaseAgent:\n"
        "    pass\n"
        "\n"
        "\n"
        "class LlmAgent(BaseAgent):\n"
        "    def __init__(self, name):\n"
        "        self.name = name\n"
        "\n"
        "    def run(self):\n"
        "        return self.name\n"
    )
    return root


def _episode(calls, context="", **kw):
    ep = {
        "context": context,
        "calls": calls,
        "steps": len(calls),
        "tokens": 500,
        "wall_seconds": 4.2,
        "ended_by": "Edit",
        "case": "CASE-1",
        "generator": "test_gen",
        "run": "run-x",
        "stream": "gen0.turn0",
    }
    ep.update(kw)
    return ep


def test_extract_symbols_from_grep_and_context():
    ep = _episode(
        [
            ("run_shell_command",
             'grep -n "class LlmAgent" .venv/lib/site-packages/google/adk/agents/llm_agent.py'),
            ("run_shell_command",
             "grep -r BuiltInCodeExecutor .venv/lib/site-packages/google/adk"),
        ],
        context="I need to find the LlmAgent constructor arguments.",
    )
    syms = extract_symbols(ep)
    assert "LlmAgent" in syms
    assert "BuiltInCodeExecutor" in syms
    # Generic words never become symbols.
    assert "class" not in syms and "the" not in syms


def test_locate_definition_module_level_assignment(sdk_corpus):
    """TypeAlias-style symbols (real SDK hunts) are located too."""
    (sdk_corpus / "agents" / "callbacks.py").write_text(
        "from typing import TypeAlias\n"
        "\n"
        "_SingleBeforeToolCallback: TypeAlias = str\n"
        "TOOL_REGISTRY = {\n"
        "    'a': 1,\n"
        "}\n"
    )
    span = locate_definition(sdk_corpus, "_SingleBeforeToolCallback")
    assert span == {
        "file": "agents/callbacks.py", "start_line": 3, "end_line": 3,
    }
    span = locate_definition(sdk_corpus, "TOOL_REGISTRY")
    assert span["start_line"] == 4 and span["end_line"] == 6


def test_locate_definition_via_ast(sdk_corpus):
    span = locate_definition(sdk_corpus, "LlmAgent")
    assert span is not None
    assert span["file"] == "agents/llm_agent.py"
    assert span["start_line"] == 5
    assert span["end_line"] == 10  # full class body
    assert locate_definition(sdk_corpus, "DoesNotExist") is None


def test_build_cases_sdk_symbol(sdk_corpus):
    ep = _episode(
        [
            ("run_shell_command",
             'grep -n "class LlmAgent" .venv/lib/python3.11/site-packages/google/adk/agents/llm_agent.py'),
            ("read_file",
             "agent-project/.venv/lib/python3.11/site-packages/google/adk/agents/llm_agent.py"),
        ],
        context="Find how LlmAgent is constructed",
    )
    cases = build_cases([ep], sdk_root=sdk_corpus, project_root=None)
    assert len(cases) == 1
    case = cases[0]
    assert case["family"] == "sdk-symbol"
    assert case["corpus"] == "sdk"
    assert "LlmAgent" in case["query"]
    assert case["expected_spans"][0]["file"] == "agents/llm_agent.py"
    # The observed (real-agent) cost rides along for comparison.
    assert case["observed"]["steps"] == 2
    assert case["observed"]["tokens"] == 500


def test_build_cases_project_file(tmp_path, sdk_corpus):
    proj = tmp_path / "proj"
    (proj / "app").mkdir(parents=True)
    (proj / "app" / "agent.py").write_text("root_agent = None\n")
    (proj / "pyproject.toml").write_text("[project]\nname='x'\n")
    ep = _episode(
        [
            ("read_file", "agent-project/app/agent.py"),
            ("read_file", "agent-project/pyproject.toml"),
        ],
        context="check the project deps and the agent entrypoint",
    )
    cases = build_cases([ep], sdk_root=sdk_corpus, project_root=proj)
    assert len(cases) == 1
    case = cases[0]
    assert case["family"] == "project-file"
    assert case["corpus"] == "project"
    files = {s["file"] for s in case["expected_spans"]}
    assert files == {"app/agent.py", "pyproject.toml"}


def test_build_cases_skips_unreplayable(sdk_corpus):
    """Env probes and symbols missing from the corpus produce no case."""
    probe = _episode(
        [("Bash", "pip show google-adk"), ("Bash", "which adk")],
        context="what is installed?",
    )
    ghost = _episode(
        [("Bash", "grep -r NoSuchSymbolAnywhere .venv/lib/site-packages/google/adk")],
        context="",
    )
    cases = build_cases([probe, ghost], sdk_root=sdk_corpus, project_root=None)
    assert cases == []
