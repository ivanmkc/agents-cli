"""Tests for the AlphaEvolve evaluate() entrypoint."""

from evolve.retrieval import evaluate as evaluate_mod
from evolve.retrieval import search_tool


def test_evaluate_returns_fitness_metrics(tmp_path):
    result = evaluate_mod.evaluate(search_tool.__file__, workdir=tmp_path, scale=1)
    assert 0.0 <= result["combined_score"] <= 1.0
    assert result["combined_score"] > 0.3
    assert {"recall", "precision", "mrr", "avg_tokens_returned"} <= set(result)


def test_benchmark_is_built_once_and_reused(tmp_path):
    evaluate_mod.evaluate(search_tool.__file__, workdir=tmp_path, scale=1)
    manifest_path = tmp_path / "repo.tasks.json"
    assert manifest_path.exists()
    mtime = manifest_path.stat().st_mtime_ns

    evaluate_mod.evaluate(search_tool.__file__, workdir=tmp_path, scale=1)
    assert manifest_path.stat().st_mtime_ns == mtime


def test_stage1_is_a_subset_screen(tmp_path):
    full = evaluate_mod.evaluate(search_tool.__file__, workdir=tmp_path, scale=1)
    stage1 = evaluate_mod.evaluate_stage1(
        search_tool.__file__, workdir=tmp_path, scale=1
    )
    assert stage1["num_tasks"] < full["num_tasks"]
    assert 0.0 <= stage1["combined_score"] <= 1.0
