"""Tests for the LocalEvaluator fitness function."""

import textwrap

import pytest
from evolve.retrieval import search_tool
from evolve.retrieval.evaluator import LocalEvaluator
from evolve.retrieval.monorepo import generate_monorepo

BLOAT_PROGRAM = textwrap.dedent(
    """
    import argparse, json, sys
    from pathlib import Path

    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--query")
    parser.add_argument("--max-results", type=int, default=8)
    parser.add_argument("--token-budget", type=int, default=2000)
    args = parser.parse_args()

    # Naive grep-style tool: return ENTIRE files that mention any term.
    terms = [t for t in args.query.split() if len(t) > 6]
    chunks = []
    for path in sorted(args.repo.rglob("*.py")):
        text = path.read_text(errors="replace")
        if any(t in text for t in terms):
            chunks.append({
                "file": path.relative_to(args.repo).as_posix(),
                "start_line": 1,
                "end_line": len(text.splitlines()),
                "content": text,
                "score": 1.0,
            })
    json.dump({"chunks": chunks[: args.max_results]}, sys.stdout)
    """
)

SLEEPY_PROGRAM = "import time\ntime.sleep(60)\n"


@pytest.fixture(scope="module")
def bench(tmp_path_factory):
    root = tmp_path_factory.mktemp("bench")
    repo = root / "repo"
    manifest = generate_monorepo(repo, seed=42, scale=2)
    return repo, manifest


def test_baseline_scores_well(bench):
    repo, manifest = bench
    evaluator = LocalEvaluator(repo, manifest)
    result = evaluator.evaluate_program(search_tool.__file__)

    assert result["num_tasks"] == len(manifest["tasks"])
    assert result["failures"] == 0
    assert result["recall"] > 0.5
    assert result["combined_score"] > 0.4
    assert 0.0 <= result["combined_score"] <= 1.0
    assert result["avg_tokens_returned"] > 0


def test_reports_per_kind_recall(bench):
    repo, manifest = bench
    result = LocalEvaluator(repo, manifest).evaluate_program(search_tool.__file__)

    by_kind = result["by_kind"]
    assert set(by_kind) == {"definition", "usage", "config"}
    for kind, stats in by_kind.items():
        assert 0.0 <= stats["recall"] <= 1.0
        assert stats["num_tasks"] == sum(
            1 for t in manifest["tasks"] if t["kind"] == kind
        )


def test_bloated_grep_scores_below_baseline(bench, tmp_path):
    repo, manifest = bench
    program = tmp_path / "bloat.py"
    program.write_text(BLOAT_PROGRAM)

    evaluator = LocalEvaluator(repo, manifest)
    baseline = evaluator.evaluate_program(search_tool.__file__)
    bloat = evaluator.evaluate_program(program)

    assert bloat["precision"] < baseline["precision"]
    assert bloat["combined_score"] < baseline["combined_score"]
    assert bloat["avg_tokens_returned"] > baseline["avg_tokens_returned"]


def test_broken_program_scores_zero(bench, tmp_path):
    repo, manifest = bench
    program = tmp_path / "broken.py"
    program.write_text("this is not valid python (")

    small = dict(manifest, tasks=manifest["tasks"][:3])
    result = LocalEvaluator(repo, small).evaluate_program(program)
    assert result["combined_score"] == 0.0
    assert result["failures"] == 3


def test_hanging_program_times_out(bench, tmp_path):
    repo, manifest = bench
    program = tmp_path / "sleepy.py"
    program.write_text(SLEEPY_PROGRAM)

    small = dict(manifest, tasks=manifest["tasks"][:2])
    result = LocalEvaluator(repo, small, timeout=2).evaluate_program(program)
    assert result["combined_score"] == 0.0
    assert result["failures"] == 2
