"""Tests for the run-vs-run retrieval comparison pipeline."""

import json
from pathlib import Path

from evolve.retrieval.compare_runs import (
    _detect_partial,
    _extract_models,
    _load_run_metadata,
    aggregate,
    categorize_episode,
)


def _ep(args, generator="Interactive_claude_agents-cli", motivation="orientation",
        steps=2, tokens=100):
    return {
        "calls": [("Bash", a) for a in args],
        "generator": generator,
        "motivation": motivation,
        "steps": steps,
        "tokens": tokens,
        "context": "",
        "case": "C",
        "stream": "gen0.turn0",
    }


def test_categorize_distinguishes_adk_cli_and_skill_reads():
    assert categorize_episode(
        _ep(["grep X .venv/lib/python3.12/site-packages/google/adk/agents.py"])
    ) == "adk-sdk"
    assert categorize_episode(
        _ep(["grep port /x/site-packages/google/agents/cli/run/_local_server.py"])
    ) == "tool-internals"
    assert categorize_episode(
        _ep(["cat /tmp/.claude/skills/google-agents-cli-adk-code/references/adk-python.md"])
    ) == "skill-docs"
    assert categorize_episode(_ep(["agents-cli eval --help"])) == "cli-help"
    assert categorize_episode(
        _ep(["cat agent-project/app/agent.py"])
    ) == "workspace"


def test_tool_internals_wins_over_generic_site_packages():
    ep = _ep([
        "ls /x/site-packages/google/agents/cli/",
        "cat agent-project/app/agent.py",
    ])
    assert categorize_episode(ep) == "tool-internals"


def test_aggregate_groups_by_harness_category_motivation():
    eps = [
        _ep(["cat agent-project/app/agent.py"], tokens=100),
        _ep(["cat agent-project/README.md"], tokens=50),
        _ep(["grep X /s/site-packages/google/adk/a.py"],
            generator="Interactive_gemini_agents-cli",
            motivation="failure-triggered", tokens=900),
    ]
    table = aggregate(eps, num_transcripts={"claude": 2, "gemini": 1})
    claude_ws = table[("claude", "workspace", "orientation")]
    assert claude_ws["episodes"] == 2
    assert claude_ws["tokens"] == 150
    gemini_sdk = table[("gemini", "adk-sdk", "failure-triggered")]
    assert gemini_sdk["episodes"] == 1
    assert gemini_sdk["tokens"] == 900


def test_agy_antigravity_skill_path_categorized_as_skill_docs():
    """C8: AGY reads skills from ``.gemini/antigravity-cli/skills/``."""
    ep = _ep(
        ["cat /work/.gemini/antigravity-cli/skills/deploy-guide/reference.md"]
    )
    assert categorize_episode(ep) == "skill-docs"


def test_load_run_metadata_present(tmp_path):
    meta = {"models": {"claude": "opus-4-6"}, "num_cases": 5}
    (tmp_path / "run_metadata.json").write_text(json.dumps(meta))
    assert _load_run_metadata(tmp_path) == meta


def test_load_run_metadata_absent(tmp_path):
    assert _load_run_metadata(tmp_path) is None


def test_load_run_metadata_corrupt(tmp_path):
    (tmp_path / "run_metadata.json").write_text("{bad json")
    assert _load_run_metadata(tmp_path) is None


def test_detect_partial_matches(tmp_path):
    meta = {"num_cases": 3}
    detail = tmp_path / "results_detail"
    detail.mkdir()
    (detail / "case1.json.gz").write_text("")
    (detail / "case2.json.gz").write_text("")
    assert _detect_partial(tmp_path, meta) is True


def test_detect_partial_complete(tmp_path):
    meta = {"num_cases": 2}
    detail = tmp_path / "results_detail"
    detail.mkdir()
    (detail / "case1.json.gz").write_text("")
    (detail / "case2.json.gz").write_text("")
    assert _detect_partial(tmp_path, meta) is False


def test_detect_partial_no_metadata(tmp_path):
    assert _detect_partial(tmp_path, None) is False


def test_detect_partial_no_num_cases(tmp_path):
    assert _detect_partial(tmp_path, {"models": {}}) is False


def test_extract_models_from_metadata():
    side_a = {
        "run_metadata": {"models": {"claude": "opus-4-6", "gemini": "flash-3"}},
        "num_transcripts": {"claude": 5, "gemini": 3},
    }
    side_b = {
        "run_metadata": {"models": {"claude": "opus-4-7", "gemini": "flash-3.5"}},
        "num_transcripts": {"claude": 5, "gemini": 3},
    }
    result = _extract_models(side_a, side_b, "a", "b")
    assert result["a"] == {"claude": "opus-4-6", "gemini": "flash-3"}
    assert result["b"] == {"claude": "opus-4-7", "gemini": "flash-3.5"}


def test_extract_models_fallback_when_no_metadata():
    side_a = {"num_transcripts": {"claude": 5}}
    side_b = {"num_transcripts": {"gemini": 3}}
    result = _extract_models(side_a, side_b, "a", "b")
    assert result["a"] == {"claude": "unknown"}
    assert result["b"] == {"gemini": "unknown"}
