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


def _real_manifest_dict(tasks, corpora=None):
    """A minimal v1 real-log manifest around the given tasks."""
    return {
        "schema_version": 1,
        "provenance": {"source_run": "test", "miner": "test"},
        "corpus_store": "corpus",
        "corpora": corpora
        or {
            "project": {
                "role": "workspace",
                "pin": {"kind": "command", "command": "scaffold", "tool_version": "t"},
            },
            "sdk": {
                "role": "dependency",
                "pin": {"kind": "package", "name": "some-sdk", "version": "1.0"},
            },
        },
        "tasks": tasks,
    }


def _real_fixture(tmp_path):
    """Tiny project corpus + v1 manifest with one task per corpus."""
    root = tmp_path / "proj"
    (root / "app").mkdir(parents=True)
    (root / "app" / "agent.py").write_text(
        "\n".join(f"line_{i} = {i}" for i in range(1, 21)) + "\n"
    )
    manifest = tmp_path / "real_manifest.json"
    manifest.write_text(
        json.dumps(
            _real_manifest_dict(
                [
                    {
                        "id": "r0",
                        "family": "workspace-file",
                        "corpus": "project",
                        "query": "check the agent file line_5 configuration",
                        "expected_spans": [
                            {"file": "app/agent.py", "start_line": 1, "end_line": 20}
                        ],
                        "pins": {"files": {}},
                        "observed": {"steps": 2, "tokens": 100, "wall_seconds": 1.0},
                    },
                    {
                        "id": "r1",
                        "family": "dependency-symbol",
                        "corpus": "sdk",
                        "query": "LlmAgent definition",
                        "expected_spans": [
                            {"file": "google/adk/agents.py", "start_line": 1, "end_line": 5}
                        ],
                        "pins": {"files": {}},
                        "observed": {"steps": 3, "tokens": 200, "wall_seconds": 2.0},
                    },
                ]
            )
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
    monkeypatch.setenv("EVOLVE_REAL_ROOTS", json.dumps({"project": str(root)}))
    result = evaluate_mod.evaluate_real(
        search_tool.__file__, manifest_path=manifest, corpus_cache=tmp_path / "cc"
    )
    assert result["num_tasks"] == 1


def test_evaluate_blends_real_score_when_env_configured(tmp_path, monkeypatch):
    root, manifest = _real_fixture(tmp_path)
    monkeypatch.setenv("EVOLVE_REAL_MANIFEST", str(manifest))
    monkeypatch.setenv("EVOLVE_REAL_ROOTS", json.dumps({"project": str(root)}))
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


def test_evaluate_real_resolves_pinned_corpus_from_store(tmp_path):
    # No corpus_roots arg, no env vars: the manifest's corpus_store +
    # pinned corpus name must be enough (hermetic evaluation).
    from evolve.retrieval import corpus_store

    tree = tmp_path / "tree"
    (tree / "app").mkdir(parents=True)
    (tree / "app" / "agent.py").write_text(
        "\n".join(f"line_{i} = {i}" for i in range(1, 21)) + "\n"
    )
    corpus_store.snapshot(tree, "project", tmp_path / "corpus")
    manifest = tmp_path / "real_manifest.json"
    manifest.write_text(
        json.dumps(
            _real_manifest_dict(
                [
                    {
                        "id": "r0",
                        "family": "workspace-file",
                        "corpus": "project",
                        "query": "check the agent file line_5 configuration",
                        "expected_spans": [
                            {"file": "app/agent.py", "start_line": 1, "end_line": 20}
                        ],
                        "pins": {"files": {}},
                        "observed": {"steps": 2, "tokens": 100, "wall_seconds": 1.0},
                    }
                ]
            )
        )
    )
    result = evaluate_mod.evaluate_real(
        search_tool.__file__,
        manifest_path=manifest,
        corpus_cache=tmp_path / "cache",
    )
    assert result["num_tasks"] == 1
    assert result["num_skipped"] == 0
    assert 0.0 <= result["combined_score"] <= 1.0


def test_evaluate_real_raises_on_pin_mismatch(tmp_path):
    """evaluate_real raises ValueError when file pins don't match disk."""
    import hashlib

    root = tmp_path / "proj"
    (root / "app").mkdir(parents=True)
    content = "\n".join(f"line_{i} = {i}" for i in range(1, 21)) + "\n"
    (root / "app" / "agent.py").write_text(content)

    # Build a manifest whose pins record a WRONG hash for agent.py
    wrong_hash = hashlib.sha256(b"wrong content").hexdigest()
    manifest = tmp_path / "real_manifest.json"
    manifest.write_text(
        json.dumps(
            _real_manifest_dict(
                [
                    {
                        "id": "r0",
                        "family": "workspace-file",
                        "corpus": "project",
                        "query": "check the agent file",
                        "expected_spans": [
                            {"file": "app/agent.py", "start_line": 1, "end_line": 20}
                        ],
                        "pins": {"files": {"app/agent.py": wrong_hash}},
                        "observed": {"steps": 2, "tokens": 100, "wall_seconds": 1.0},
                    }
                ]
            )
        )
    )
    with pytest.raises(ValueError, match="pin.*mismatch"):
        evaluate_mod.evaluate_real(
            search_tool.__file__,
            manifest_path=manifest,
            corpus_roots={"project": root},
        )


def test_evaluate_real_explicit_roots_override_store(tmp_path):
    from evolve.retrieval import corpus_store

    tree = tmp_path / "tree"
    (tree / "app").mkdir(parents=True)
    (tree / "app" / "agent.py").write_text("x = 1\n")
    corpus_store.snapshot(tree, "project", tmp_path / "corpus")
    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps(
            _real_manifest_dict(
                [
                    {
                        "id": "r0",
                        "family": "workspace-file",
                        "corpus": "project",
                        "query": "x",
                        "expected_spans": [
                            {"file": "app/agent.py", "start_line": 1, "end_line": 1}
                        ],
                        "pins": {"files": {}},
                        "observed": {"steps": 1, "tokens": 10, "wall_seconds": 0.5},
                    }
                ]
            )
        )
    )
    override = tmp_path / "elsewhere"
    (override / "app").mkdir(parents=True)
    (override / "app" / "agent.py").write_text("x = 1\n")
    result = evaluate_mod.evaluate_real(
        search_tool.__file__,
        manifest_path=manifest,
        corpus_roots={"project": override},
        corpus_cache=tmp_path / "cache",
    )
    assert result["num_tasks"] == 1
    assert not (tmp_path / "cache").exists()  # store never touched
