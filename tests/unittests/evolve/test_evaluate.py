"""Tests for the AlphaEvolve evaluate() entrypoint."""

from evolve.retrieval import evaluate as evaluate_mod
from evolve.retrieval import search_tool


def test_evaluate_returns_fitness_metrics(tmp_path):
    result = evaluate_mod.evaluate(search_tool.__file__, workdir=tmp_path, scale=1)
    assert 0.0 <= result["combined_score"] <= 1.0
    assert result["combined_score"] > 0.3
    assert {"recall", "precision", "mrr", "avg_tokens_returned"} <= set(result)


def test_benchmark_is_deterministic_across_calls(tmp_path):
    first = evaluate_mod.evaluate(search_tool.__file__, workdir=tmp_path, scale=1)
    second = evaluate_mod.evaluate(search_tool.__file__, workdir=tmp_path, scale=1)
    assert first == second
    # The answer key must never touch disk during evaluation (red-team
    # audit: a candidate read it and scored a perfect 1.0).
    assert not (tmp_path / "repo.tasks.json").exists()


def test_stage1_is_a_subset_screen(tmp_path):
    full = evaluate_mod.evaluate(search_tool.__file__, workdir=tmp_path, scale=1)
    stage1 = evaluate_mod.evaluate_stage1(
        search_tool.__file__, workdir=tmp_path, scale=1
    )
    assert stage1["num_tasks"] < full["num_tasks"]
    assert 0.0 <= stage1["combined_score"] <= 1.0
