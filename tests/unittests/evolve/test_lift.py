"""Tests for lift measurement against the default (grep-style) behavior."""

import json

import pytest
from evolve.retrieval import baselines, search_tool
from evolve.retrieval import evaluate as evaluate_mod
from evolve.retrieval.evaluator import LocalEvaluator, compute_lift
from evolve.retrieval.monorepo import generate_monorepo

DEFAULT_TOOL = baselines.DEFAULT_GREP_PATH


@pytest.fixture(scope="module")
def bench(tmp_path_factory):
    root = tmp_path_factory.mktemp("bench")
    repo = root / "repo"
    manifest = generate_monorepo(repo, seed=42, scale=2)
    return repo, manifest


def test_default_grep_emulates_todays_bloat(bench):
    """The reference tool must reproduce the failure mode we're fixing:
    it finds the code (recall) but drags in whole files (tokens)."""
    repo, manifest = bench
    evaluator = LocalEvaluator(repo, manifest)
    default = evaluator.evaluate_program(DEFAULT_TOOL)
    seed = evaluator.evaluate_program(search_tool.__file__)

    assert default["failures"] == 0
    assert default["recall"] > 0.5
    assert default["avg_tokens_returned"] > 2 * seed["avg_tokens_returned"]
    assert default["precision"] < seed["precision"]
    assert default["combined_score"] < seed["combined_score"]


def test_compute_lift_math():
    candidate = {
        "combined_score": 0.75,
        "recall": 0.9,
        "precision": 0.5,
        "mrr": 0.8,
        "avg_tokens_returned": 500.0,
    }
    default = {
        "combined_score": 0.5,
        "recall": 0.8,
        "precision": 0.1,
        "mrr": 0.6,
        "avg_tokens_returned": 2000.0,
    }
    lift = compute_lift(candidate, default)
    assert lift["combined_score_delta"] == pytest.approx(0.25)
    assert lift["combined_score_lift_pct"] == pytest.approx(50.0)
    assert lift["token_reduction_pct"] == pytest.approx(75.0)
    assert lift["recall_delta"] == pytest.approx(0.1)
    assert lift["precision_delta"] == pytest.approx(0.4)
    assert lift["mrr_delta"] == pytest.approx(0.2)


def test_evaluate_lift_reports_and_caches_default(tmp_path):
    report = evaluate_mod.evaluate_lift(search_tool.__file__, workdir=tmp_path, scale=1)
    assert set(report) == {"candidate", "default", "lift"}
    assert report["lift"]["token_reduction_pct"] > 0

    # Default metrics are cached in the workdir and reused verbatim.
    cache = tmp_path / "default_metrics.json"
    assert cache.exists()
    mtime = cache.stat().st_mtime_ns
    again = evaluate_mod.evaluate_lift(search_tool.__file__, workdir=tmp_path, scale=1)
    assert cache.stat().st_mtime_ns == mtime
    assert again["default"] == report["default"]
    assert json.loads(cache.read_text())["combined_score"] == pytest.approx(
        report["default"]["combined_score"]
    )
