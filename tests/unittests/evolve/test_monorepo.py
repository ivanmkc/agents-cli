"""Tests for the mock-monorepo + benchmark-task generator."""

import json

from evolve.retrieval.monorepo import generate_monorepo


def test_generation_is_deterministic(tmp_path):
    repo_a = tmp_path / "a"
    repo_b = tmp_path / "b"
    manifest_a = generate_monorepo(repo_a, seed=7, scale=2)
    manifest_b = generate_monorepo(repo_b, seed=7, scale=2)

    files_a = sorted(p.relative_to(repo_a) for p in repo_a.rglob("*") if p.is_file())
    files_b = sorted(p.relative_to(repo_b) for p in repo_b.rglob("*") if p.is_file())
    assert files_a == files_b
    assert [t["query"] for t in manifest_a["tasks"]] == [
        t["query"] for t in manifest_b["tasks"]
    ]


def test_tasks_reference_real_spans(tmp_path):
    repo = tmp_path / "repo"
    manifest = generate_monorepo(repo, seed=1, scale=2)

    assert len(manifest["tasks"]) > 0
    for task in manifest["tasks"]:
        assert task["query"]
        assert task["kind"] in {"definition", "usage", "config"}
        assert len(task["expected_spans"]) > 0
        for span in task["expected_spans"]:
            target = repo / span["file"]
            assert target.is_file(), f"missing {span['file']}"
            n_lines = len(target.read_text().splitlines())
            assert 1 <= span["start_line"] <= span["end_line"] <= n_lines


def test_definition_span_contains_symbol(tmp_path):
    repo = tmp_path / "repo"
    manifest = generate_monorepo(repo, seed=3, scale=2)

    definition_tasks = [t for t in manifest["tasks"] if t["kind"] == "definition"]
    assert definition_tasks
    for task in definition_tasks:
        span = task["expected_spans"][0]
        lines = (repo / span["file"]).read_text().splitlines()
        span_text = "\n".join(lines[span["start_line"] - 1 : span["end_line"]])
        assert task["symbol"] in span_text


def test_manifest_written_to_disk_and_has_noise(tmp_path):
    repo = tmp_path / "repo"
    manifest = generate_monorepo(repo, seed=5, scale=2)

    # The answer key lives OUTSIDE the searchable tree so an evolved
    # search tool cannot cheat by reading it.
    on_disk = json.loads((tmp_path / "repo.tasks.json").read_text())
    assert on_disk == manifest
    assert not (repo / "repo.tasks.json").exists()

    # Noise files exist so that naive full-file retrieval is penalized.
    vendor_files = list((repo / "vendor").rglob("*.py"))
    assert vendor_files
    assert any(len(p.read_text().splitlines()) > 500 for p in vendor_files)


def test_default_scale_yields_full_benchmark(tmp_path):
    manifest = generate_monorepo(tmp_path / "repo", seed=0)
    assert len(manifest["tasks"]) >= 100
