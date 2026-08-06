"""Tests for the AlphaEvolve evaluate() entrypoint."""

import json

import pytest

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


def _real_fixture(tmp_path):
    """Tiny project corpus + validated-style real-log manifest."""
    root = tmp_path / "proj"
    (root / "app").mkdir(parents=True)
    (root / "app" / "agent.py").write_text(
        "\n".join(f"line_{i} = {i}" for i in range(1, 21)) + "\n"
    )
    manifest = tmp_path / "real_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "family": "project-file",
                        "kind": "project-file",
                        "corpus": "project",
                        "query": "check the agent file line_5 configuration",
                        "expected_spans": [
                            {"file": "app/agent.py", "start_line": 1, "end_line": 20}
                        ],
                        "observed": {"steps": 2, "tokens": 100, "wall_seconds": 1.0},
                    },
                    {
                        "family": "sdk-symbol",
                        "kind": "sdk-symbol",
                        "corpus": "sdk",
                        "query": "LlmAgent definition",
                        "expected_spans": [
                            {"file": "google/adk/agents.py", "start_line": 1, "end_line": 5}
                        ],
                        "observed": {"steps": 3, "tokens": 200, "wall_seconds": 2.0},
                    },
                ]
            }
        )
    )
    return root, manifest


def test_evaluate_real_scores_tasks_against_corpus_roots(tmp_path):
    root, manifest = _real_fixture(tmp_path)
    result = evaluate_mod.evaluate_real(
        search_tool.__file__,
        manifest_path=manifest,
        corpus_roots={"project": root},
    )
    assert result["num_tasks"] == 1  # sdk task skipped: no sdk root
    assert result["num_skipped"] == 1
    assert 0.0 <= result["combined_score"] <= 1.0
    assert result["per_corpus"]["project"]["num_tasks"] == 1


def test_evaluate_real_missing_manifest_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        evaluate_mod.evaluate_real(
            search_tool.__file__,
            manifest_path=tmp_path / "nope.json",
            corpus_roots={"project": tmp_path},
        )


def test_evaluate_real_corpus_roots_from_env(tmp_path, monkeypatch):
    root, manifest = _real_fixture(tmp_path)
    monkeypatch.setenv("EVOLVE_REAL_PROJECT_ROOT", str(root))
    result = evaluate_mod.evaluate_real(search_tool.__file__, manifest_path=manifest)
    assert result["num_tasks"] == 1


def test_evaluate_blends_real_score_when_env_configured(tmp_path, monkeypatch):
    root, manifest = _real_fixture(tmp_path)
    monkeypatch.setenv("EVOLVE_REAL_MANIFEST", str(manifest))
    monkeypatch.setenv("EVOLVE_REAL_PROJECT_ROOT", str(root))
    monkeypatch.setenv("EVOLVE_REAL_WEIGHT", "0.5")
    blended = evaluate_mod.evaluate(
        search_tool.__file__, workdir=tmp_path / "wd", scale=1
    )
    assert blended["real"]["num_tasks"] == 1
    expected = 0.5 * blended["mock_combined_score"] + 0.5 * blended["real"]["combined_score"]
    assert blended["combined_score"] == pytest.approx(expected, abs=1e-4)


def test_evaluate_unchanged_when_real_weight_unset(tmp_path):
    result = evaluate_mod.evaluate(search_tool.__file__, workdir=tmp_path, scale=1)
    assert "real" not in result
