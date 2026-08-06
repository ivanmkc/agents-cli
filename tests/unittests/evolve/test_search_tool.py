"""Tests for the evolvable code-search tool's stable contract."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from evolve.retrieval import search_tool
from evolve.retrieval.monorepo import generate_monorepo

TOOL_PATH = Path(search_tool.__file__)


@pytest.fixture(scope="module")
def bench(tmp_path_factory):
    root = tmp_path_factory.mktemp("bench")
    repo = root / "repo"
    manifest = generate_monorepo(repo, seed=11, scale=2)
    return repo, manifest


def _overlaps(chunk, span):
    return (
        chunk["file"] == span["file"]
        and chunk["start_line"] <= span["end_line"]
        and chunk["end_line"] >= span["start_line"]
    )


def test_finds_known_definition(bench):
    repo, manifest = bench
    task = next(t for t in manifest["tasks"] if t["kind"] == "definition")
    chunks = search_tool.search(repo, task["query"])
    assert chunks, "expected at least one result"
    span = task["expected_spans"][0]
    assert any(_overlaps(c, span) for c in chunks[:3]), (
        f"top results missed {span}: got "
        f"{[(c['file'], c['start_line'], c['end_line']) for c in chunks[:3]]}"
    )


def test_respects_token_budget_and_max_results(bench):
    repo, _ = bench
    chunks = search_tool.search(
        repo, "process request token", max_results=3, token_budget=200
    )
    assert len(chunks) <= 3
    total = sum(search_tool.estimate_tokens(c["content"]) for c in chunks)
    assert total <= 200


def test_no_match_returns_empty(bench):
    repo, _ = bench
    assert search_tool.search(repo, "zzqx_nonexistent_symbol_477") == []


def test_chunk_schema(bench):
    repo, manifest = bench
    chunks = search_tool.search(repo, manifest["tasks"][0]["query"])
    for chunk in chunks:
        assert set(chunk) == {"file", "start_line", "end_line", "content", "score"}
        assert not Path(chunk["file"]).is_absolute()
        assert chunk["start_line"] <= chunk["end_line"]


def test_cli_contract(bench):
    repo, manifest = bench
    task = next(t for t in manifest["tasks"] if t["kind"] == "definition")
    proc = subprocess.run(
        [
            sys.executable,
            str(TOOL_PATH),
            "--repo",
            str(repo),
            "--query",
            task["query"],
            "--max-results",
            "5",
            "--token-budget",
            "1500",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert "chunks" in payload
    assert len(payload["chunks"]) <= 5
