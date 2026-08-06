"""Regression tests for exploits found by the red-team audit.

Each test encodes a demonstrated reward-hack; the hardened fitness
function must keep every one of them strictly below the honest seed.
"""

import json
import sys
import textwrap
from pathlib import Path

import pytest
from evolve.retrieval import evaluate as evaluate_mod
from evolve.retrieval import search_tool
from evolve.retrieval.evaluator import LocalEvaluator
from evolve.retrieval.monorepo import generate_monorepo

# Exploit 1 (audit: "degenerate 1-line-chunk tool"): return only the
# single best-matching line per file. Under binary any-overlap recall
# this scored 0.85-0.88 vs the seed's 0.74.
ONE_LINE_TOOL = textwrap.dedent(
    """
    import argparse, json, re, sys
    from pathlib import Path

    p = argparse.ArgumentParser()
    p.add_argument("--repo", type=Path); p.add_argument("--query")
    p.add_argument("--max-results", type=int, default=8)
    p.add_argument("--token-budget", type=int, default=2000)
    a = p.parse_args()
    terms = [t for t in re.findall(r"\\w+", a.query) if len(t) > 6]
    chunks = []
    for path in sorted(a.repo.rglob("*")):
        if not path.is_file() or path.suffix not in {".py", ".yaml"}:
            continue
        lines = path.read_text(errors="replace").splitlines()
        for i, line in enumerate(lines, 1):
            if any(t in line for t in terms):
                chunks.append({"file": path.relative_to(a.repo).as_posix(),
                               "start_line": i, "end_line": i,
                               "content": line, "score": 1.0})
                break
    json.dump({"chunks": chunks[: a.max_results]}, sys.stdout)
    """
)

# Exploit 2 (audit: "fitness fully gameable, fake content"): claim huge
# spans with empty content — previously earned recall at zero token cost.
FABRICATOR_TOOL = textwrap.dedent(
    """
    import argparse, json, sys
    from pathlib import Path

    p = argparse.ArgumentParser()
    p.add_argument("--repo", type=Path); p.add_argument("--query")
    p.add_argument("--max-results", type=int, default=8)
    p.add_argument("--token-budget", type=int, default=2000)
    a = p.parse_args()
    chunks = [{"file": f.relative_to(a.repo).as_posix(), "start_line": 1,
               "end_line": 10**6, "content": "", "score": 1.0}
              for f in sorted(a.repo.rglob("*.py"))[:8]]
    json.dump({"chunks": chunks}, sys.stdout)
    """
)

# Exploit 3 (audit: "answer key readable, cheat scores exactly 1.0"):
# read <repo>.tasks.json from the repo's parent directory.
KEY_READER_TOOL = textwrap.dedent(
    """
    import argparse, json, sys
    from pathlib import Path

    p = argparse.ArgumentParser()
    p.add_argument("--repo", type=Path); p.add_argument("--query")
    p.add_argument("--max-results", type=int, default=8)
    p.add_argument("--token-budget", type=int, default=2000)
    a = p.parse_args()
    repo = a.repo.resolve()
    chunks = []
    key = repo.parent / (repo.name + ".tasks.json")
    if key.exists():
        for task in json.loads(key.read_text())["tasks"]:
            if task["query"] == a.query:
                for s in task["expected_spans"]:
                    lines = (repo / s["file"]).read_text().splitlines()
                    content = "\\n".join(lines[s["start_line"] - 1 : s["end_line"]])
                    chunks.append({**s, "content": content, "score": 9.9})
    json.dump({"chunks": chunks}, sys.stdout)
    """
)


@pytest.fixture(scope="module")
def bench(tmp_path_factory):
    root = tmp_path_factory.mktemp("bench")
    repo = root / "repo"
    manifest = generate_monorepo(repo, seed=42, scale=2, write_tasks=False)
    return repo, manifest


@pytest.fixture(scope="module")
def seed_score(bench):
    repo, manifest = bench
    return LocalEvaluator(repo, manifest).evaluate_program(search_tool.__file__)


def _score(bench, tmp_path, code: str) -> dict:
    repo, manifest = bench
    program = tmp_path / "exploit.py"
    program.write_text(code)
    return LocalEvaluator(repo, manifest).evaluate_program(program)


def test_one_line_degenerate_scores_below_seed(bench, tmp_path, seed_score):
    result = _score(bench, tmp_path, ONE_LINE_TOOL)
    assert result["combined_score"] < seed_score["combined_score"]
    # Coverage-weighted recall is what kills it.
    assert result["recall"] < 0.5


def test_fabricated_content_earns_nothing(bench, tmp_path):
    result = _score(bench, tmp_path, FABRICATOR_TOOL)
    assert result["recall"] == 0.0
    assert result["precision"] == 0.0
    assert result["mrr"] == 0.0
    # ...and the claimed spans still cost tokens (computed from disk).
    assert result["avg_tokens_returned"] > 0


def test_no_answer_key_exists_during_evaluation(tmp_path):
    evaluate_mod.evaluate(search_tool.__file__, workdir=tmp_path, scale=1)
    assert not list(tmp_path.rglob("*tasks*.json"))


def test_key_reader_gains_nothing(bench, tmp_path):
    """With no key on disk the cheat degenerates to returning nothing."""
    result = _score(bench, tmp_path, KEY_READER_TOOL)
    assert result["combined_score"] == 0.0


def test_stage1_screen_is_kind_stratified(tmp_path):
    result = evaluate_mod.evaluate_stage1(
        search_tool.__file__, workdir=tmp_path, scale=1
    )
    by_kind = result["by_kind"]
    assert set(by_kind) == {"definition", "usage", "config"}


# ---- Real-eval env-leak regression tests (C1) ----
#
# A candidate tool that reads HOME, VIRTUAL_ENV, or PYTHONHOME can
# locate the committed real_eval_manifest.validated.json file and
# inflate its score 2.28x. These tests verify those variables are NOT
# forwarded to the subprocess.

ENV_PROBE_TOOL = textwrap.dedent(
    """
    import argparse, json, os, sys

    p = argparse.ArgumentParser()
    p.add_argument("--repo", type=str)
    p.add_argument("--query")
    p.add_argument("--max-results", type=int, default=8)
    p.add_argument("--token-budget", type=int, default=2000)
    a = p.parse_args()

    result = {
        "has_HOME": "HOME" in os.environ,
        "has_VIRTUAL_ENV": "VIRTUAL_ENV" in os.environ,
        "has_PYTHONHOME": "PYTHONHOME" in os.environ,
        "env_keys": sorted(os.environ.keys()),
    }

    try:
        import evolve.retrieval.evaluate
        result["can_import_evolve"] = True
    except ImportError:
        result["can_import_evolve"] = False

    probe_path = os.path.join(a.repo, ".env_probe_result")
    with open(probe_path, "w") as f:
        json.dump(result, f)

    json.dump({"chunks": []}, sys.stdout)
    """
)


def _run_env_probe(bench, tmp_path, monkeypatch):
    """Run the env-probe tool once and return the captured result dict."""
    repo, manifest = bench
    # Ensure the leaky vars exist in the parent so we can verify they
    # are NOT forwarded to the subprocess.
    monkeypatch.setenv("HOME", "/fake/home")
    monkeypatch.setenv("VIRTUAL_ENV", "/fake/venv")
    # Use the real Python prefix so the subprocess can start even if
    # PYTHONHOME leaks (before the fix).
    monkeypatch.setenv("PYTHONHOME", sys.prefix)
    # Set PYTHONPATH to include the tools/ dir (contains the evolve package).
    tools_dir = str(Path(__file__).resolve().parents[3] / "tools")
    monkeypatch.setenv("PYTHONPATH", tools_dir)

    program = tmp_path / "env_probe.py"
    program.write_text(ENV_PROBE_TOOL)
    probe_file = repo / ".env_probe_result"
    try:
        LocalEvaluator(repo, manifest, max_workers=1).evaluate_program(program)
        assert probe_file.exists(), "env probe tool did not write results"
        return json.loads(probe_file.read_text())
    finally:
        probe_file.unlink(missing_ok=True)


def test_home_not_in_subprocess_env(bench, tmp_path, monkeypatch):
    """HOME must not be forwarded — it lets a candidate find the manifest."""
    result = _run_env_probe(bench, tmp_path, monkeypatch)
    assert not result["has_HOME"], (
        "HOME was forwarded to the subprocess; a candidate could use it "
        "to locate real_eval_manifest.validated.json"
    )


def test_virtual_env_not_in_subprocess_env(bench, tmp_path, monkeypatch):
    """VIRTUAL_ENV must not be forwarded — it exposes the venv path."""
    result = _run_env_probe(bench, tmp_path, monkeypatch)
    assert not result["has_VIRTUAL_ENV"], (
        "VIRTUAL_ENV was forwarded to the subprocess; a candidate could "
        "traverse from the venv to the repo tree"
    )


def test_pythonhome_not_in_subprocess_env(bench, tmp_path, monkeypatch):
    """PYTHONHOME must not be forwarded — it exposes the Python install."""
    result = _run_env_probe(bench, tmp_path, monkeypatch)
    assert not result["has_PYTHONHOME"], (
        "PYTHONHOME was forwarded to the subprocess"
    )


def test_candidate_cannot_import_evolve_package(bench, tmp_path, monkeypatch):
    """PYTHONPATH must not expose the evolve package to candidates."""
    result = _run_env_probe(bench, tmp_path, monkeypatch)
    assert not result["can_import_evolve"], (
        "Candidate was able to 'import evolve.retrieval.evaluate' — "
        "PYTHONPATH leaks the repo's tools/ directory"
    )
