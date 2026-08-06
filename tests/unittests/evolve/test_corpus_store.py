"""Tests for the content-addressed corpus store behind the real-log eval set.

Each real-log eval row is pinned to the exact corpus tree its ground-truth
spans were validated against. The store holds deterministic archives +
per-file hashes; materialization verifies both, so evaluation is hermetic —
no dependence on whatever agents-cli / google-adk the host has installed.
"""

import json

import pytest

from evolve.retrieval.corpus_store import materialize, snapshot


def _make_tree(root):
    (root / "app").mkdir(parents=True)
    (root / "app" / "agent.py").write_text("root_agent = 1\n")
    (root / "README.md").write_text("# demo\n")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "junk.pyc").write_bytes(b"\x00")
    return root


def test_snapshot_is_deterministic(tmp_path):
    tree = _make_tree(tmp_path / "tree")
    a = snapshot(tree, "demo", tmp_path / "store_a")
    b = snapshot(tree, "demo", tmp_path / "store_b")
    assert a["sha256"] == b["sha256"]
    assert a["files"] == b["files"]


def test_snapshot_excludes_caches_and_records_file_hashes(tmp_path):
    tree = _make_tree(tmp_path / "tree")
    entry = snapshot(tree, "demo", tmp_path / "store")
    assert "app/agent.py" in entry["files"]
    assert not any("__pycache__" in f for f in entry["files"])
    index = json.loads((tmp_path / "store" / "index.json").read_text())
    assert index["demo"]["sha256"] == entry["sha256"]


def test_materialize_roundtrip(tmp_path):
    tree = _make_tree(tmp_path / "tree")
    snapshot(tree, "demo", tmp_path / "store")
    root = materialize("demo", tmp_path / "store", tmp_path / "cache")
    assert (root / "app" / "agent.py").read_text() == "root_agent = 1\n"
    assert (root / "README.md").read_text() == "# demo\n"


def test_materialize_rejects_tampered_archive(tmp_path):
    tree = _make_tree(tmp_path / "tree")
    entry = snapshot(tree, "demo", tmp_path / "store")
    archive = tmp_path / "store" / entry["archive"]
    archive.write_bytes(archive.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="sha256"):
        materialize("demo", tmp_path / "store", tmp_path / "cache")


def test_materialize_unknown_corpus_raises(tmp_path):
    (tmp_path / "store").mkdir()
    (tmp_path / "store" / "index.json").write_text("{}")
    with pytest.raises(KeyError):
        materialize("nope", tmp_path / "store", tmp_path / "cache")
