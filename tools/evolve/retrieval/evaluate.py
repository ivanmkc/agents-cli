"""AlphaEvolve entrypoint for the retrieval-layer evolution loop.

AlphaEvolve (or an OpenEvolve-compatible runner) imports this module and
calls ``evaluate(program_path)`` for every mutated copy of
``search_tool.py``. The returned dict's ``combined_score`` is the
fitness signal.

The mock monorepo + benchmark manifest are generated once per workdir
(deterministic seed) and reused across the thousands of candidate
evaluations in a run, so scores stay comparable between generations.

A two-stage cascade is provided for cheap early rejection:

* ``evaluate_stage1`` — a small task subset; kills obviously broken
  mutants in ~a second.
* ``evaluate_stage2`` / ``evaluate`` — the full benchmark.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import sys
import tempfile
from pathlib import Path

try:
    from evolve.retrieval import baselines, corpus_store
    from evolve.retrieval.evaluator import LocalEvaluator, compute_lift
    from evolve.retrieval.monorepo import generate_monorepo
    from evolve.retrieval.schema import RealEvalManifest
except ImportError:  # invoked standalone by an evolution runner
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from evolve.retrieval import baselines, corpus_store
    from evolve.retrieval.evaluator import LocalEvaluator, compute_lift
    from evolve.retrieval.monorepo import generate_monorepo
    from evolve.retrieval.schema import RealEvalManifest

DEFAULT_SEED = 0
DEFAULT_SCALE = 3
_STAGE1_TASKS = 20

# Real-log stage (mined from agent-generator transcripts, consensus-
# validated — see real_data/validation/). The mock-evolved tool scored
# 1.0 on mock but 0.03 recall on real logs, so real cases must be able
# to gate fitness.
_REAL_MANIFEST_DEFAULT = (
    Path(__file__).resolve().parent
    / "real_data" / "real_eval_manifest.validated.json"
)
# JSON object mapping corpus name -> root dir, e.g.
# EVOLVE_REAL_ROOTS='{"adk-sdk": "/path/to/site-packages/google/adk"}'
_REAL_ROOTS_ENV = "EVOLVE_REAL_ROOTS"


def _default_workdir(seed: int, scale: int) -> Path:
    override = os.environ.get("EVOLVE_RETRIEVAL_WORKDIR")
    if override:
        return Path(override)
    return (
        Path(tempfile.gettempdir())
        / f"agents-cli-evolve-retrieval-s{seed}-x{scale}"
    )


def _ensure_benchmark(workdir: Path, seed: int, scale: int) -> tuple[Path, dict]:
    """(Re)build the mock monorepo; keep the answer key OFF disk.

    The tree is regenerated deterministically on every call (cheap, and
    self-heals stale/partial workdirs). The task manifest only ever
    lives in this process's memory: the red-team audit demonstrated a
    candidate scoring a perfect 1.0 by reading a manifest written next
    to the repo, so no ``*.tasks.json`` may exist anywhere under the
    workdir during evaluation.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    repo = workdir / "repo"
    manifest = generate_monorepo(repo, seed=seed, scale=scale, write_tasks=False)
    stale_key = workdir / "repo.tasks.json"
    stale_key.unlink(missing_ok=True)
    return repo, manifest


def _run(
    program_path: str,
    workdir: Path | str | None,
    seed: int,
    scale: int,
    n_tasks: int | None,
) -> dict:
    workdir = Path(workdir) if workdir else _default_workdir(seed, scale)
    repo, manifest = _ensure_benchmark(workdir, seed, scale)
    if n_tasks is not None:
        manifest = dict(manifest, tasks=_stratified(manifest["tasks"], n_tasks))
    return LocalEvaluator(repo, manifest).evaluate_program(program_path)


def _stratified(tasks: list[dict], n: int) -> list[dict]:
    """Deterministic, kind-proportional subset for the stage-1 screen.

    A plain ``[:n]`` slice was all definition tasks from one package —
    no config/usage/ranking signal, so stage 1 couldn't reject mutants
    that broke those paths.
    """
    rng = random.Random(1234)
    by_kind: dict[str, list[dict]] = {}
    for task in tasks:
        by_kind.setdefault(task["kind"], []).append(task)
    picked: list[dict] = []
    for kind in sorted(by_kind):
        share = max(1, round(n * len(by_kind[kind]) / len(tasks)))
        picked.extend(rng.sample(by_kind[kind], min(share, len(by_kind[kind]))))
    return picked[:n]


def evaluate_stage1(
    program_path: str,
    workdir: Path | str | None = None,
    seed: int = DEFAULT_SEED,
    scale: int = DEFAULT_SCALE,
) -> dict:
    """Fast screen on a small task subset — rejects broken mutants."""
    return _run(program_path, workdir, seed, scale, _STAGE1_TASKS)


def evaluate_stage2(
    program_path: str,
    workdir: Path | str | None = None,
    seed: int = DEFAULT_SEED,
    scale: int = DEFAULT_SCALE,
) -> dict:
    """Full benchmark — the authoritative fitness score."""
    return _run(program_path, workdir, seed, scale, None)


def evaluate_real(
    program_path: str,
    manifest_path: Path | str | None = None,
    corpus_roots: dict[str, Path | str] | None = None,
    corpus_cache: Path | str | None = None,
) -> dict:
    """Score a candidate against the validated real-log eval cases.

    Corpus resolution order, per corpus name declared in the manifest:

    1. explicit ``corpus_roots`` argument,
    2. the ``EVOLVE_REAL_ROOTS`` env var (JSON: corpus name -> root dir),
    3. the manifest's pinned corpus store (``corpus_store`` key, a
       directory relative to the manifest) — content-addressed archives
       materialized via :mod:`evolve.retrieval.corpus_store`, verified
       against the pinned sha256s. This is the hermetic default: rows
       score against the exact trees their ground truth was validated
       on, regardless of what the host has installed.

    Tasks whose corpus resolves nowhere are skipped and counted in
    ``num_skipped`` — metrics are the task-weighted mean of the rest.
    """
    manifest_path = Path(
        manifest_path
        or os.environ.get("EVOLVE_REAL_MANIFEST")
        or _REAL_MANIFEST_DEFAULT
    )
    manifest = RealEvalManifest.from_dict(json.loads(manifest_path.read_text()))
    if corpus_roots is not None:
        roots = {k: Path(v) for k, v in corpus_roots.items()}
    else:
        roots = {
            name: Path(root)
            for name, root in json.loads(
                os.environ.get(_REAL_ROOTS_ENV) or "{}"
            ).items()
        }
        store_dir = manifest_path.parent / manifest.corpus_store
        index = corpus_store.load_index(store_dir)
        cache = Path(
            corpus_cache
            or Path(tempfile.gettempdir()) / "agents-cli-evolve-corpora"
        )
        for name in manifest.corpora:
            if name not in roots and name in index:
                roots[name] = corpus_store.materialize(name, store_dir, cache)
    # C11 fix: verify per-row file pins BEFORE scoring so corpus version
    # mismatches are caught early rather than silently skewing results.
    for task in manifest.tasks:
        if task.corpus not in roots or not task.pins:
            continue
        corpus_root = roots[task.corpus]
        for rel_path, expected_hash in task.pins.items():
            file_path = corpus_root / rel_path
            if not file_path.exists():
                raise ValueError(
                    f"task {task.id}: pin mismatch — file {rel_path!r} "
                    f"does not exist in corpus {task.corpus!r}"
                )
            actual_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()
            if actual_hash != expected_hash:
                raise ValueError(
                    f"task {task.id}: pin mismatch for {rel_path!r} in "
                    f"corpus {task.corpus!r} "
                    f"(expected={expected_hash[:12]}..., "
                    f"actual={actual_hash[:12]}...)"
                )

    _METRIC_KEYS = ("combined_score", "recall", "precision", "mrr")
    cells: dict[tuple[str, str], dict] = {}
    for corpus, root in sorted(roots.items()):
        families_in_corpus = sorted(
            {t.family for t in manifest.tasks if t.corpus == corpus}
        )
        for family in families_in_corpus:
            subset = [
                t.to_evaluator_dict()
                for t in manifest.tasks
                if t.corpus == corpus and t.family == family
            ]
            if not subset:
                continue
            metrics = LocalEvaluator(root, {"tasks": subset}).evaluate_program(
                program_path
            )
            cells[(corpus, family)] = metrics

    per_corpus: dict[str, dict] = {}
    for corpus in sorted(roots):
        corpus_cells = [m for (c, _), m in cells.items() if c == corpus]
        if not corpus_cells:
            continue
        n = sum(m["num_tasks"] for m in corpus_cells)
        if not n:
            continue
        agg = {
            k: round(
                sum(m.get(k, 0.0) * m["num_tasks"] for m in corpus_cells) / n,
                4,
            )
            for k in _METRIC_KEYS
        }
        agg["num_tasks"] = n
        per_corpus[corpus] = agg

    per_family: dict[str, dict] = {}
    for family in sorted({f for _, f in cells}):
        fam_cells = [m for (_, f), m in cells.items() if f == family]
        n = sum(m["num_tasks"] for m in fam_cells)
        if not n:
            continue
        agg = {
            k: round(
                sum(m.get(k, 0.0) * m["num_tasks"] for m in fam_cells) / n, 4
            )
            for k in _METRIC_KEYS
        }
        agg["num_tasks"] = n
        per_family[family] = agg

    num_families = len(per_family) or 1
    num_tasks = sum(fm["num_tasks"] for fm in per_family.values())
    totals = {
        k: round(sum(fm[k] for fm in per_family.values()) / num_families, 4)
        for k in _METRIC_KEYS
    }
    return {
        "num_tasks": num_tasks,
        "num_skipped": len(manifest.tasks) - num_tasks,
        "per_corpus": per_corpus,
        "per_family": per_family,
        **totals,
    }


def evaluate(
    program_path: str,
    workdir: Path | str | None = None,
    seed: int = DEFAULT_SEED,
    scale: int = DEFAULT_SCALE,
) -> dict:
    """Main AlphaEvolve fitness function.

    Stage 2 on the mock monorepo; when ``EVOLVE_REAL_WEIGHT`` is set to
    a positive float (and real corpus roots are configured), the
    real-log stage is blended in:
    ``combined = (1-w)*mock + w*real`` — so candidates can no longer
    win by overfitting the mock benchmark alone.
    """
    result = evaluate_stage2(program_path, workdir, seed, scale)
    weight = float(os.environ.get("EVOLVE_REAL_WEIGHT") or 0.0)
    if weight <= 0.0:
        return result
    real = evaluate_real(program_path)
    if not real["num_tasks"]:
        return result
    result = dict(result)
    result["mock_combined_score"] = result["combined_score"]
    result["real"] = real
    result["combined_score"] = round(
        (1.0 - weight) * result["mock_combined_score"]
        + weight * real["combined_score"],
        4,
    )
    return result


def evaluate_default(
    workdir: Path | str | None = None,
    seed: int = DEFAULT_SEED,
    scale: int = DEFAULT_SCALE,
) -> dict:
    """Metrics for the default-behavior proxy (grep + whole files).

    Computed once per workdir and cached — the default never changes,
    so every candidate in a run is compared against identical numbers.
    """
    workdir = Path(workdir) if workdir else _default_workdir(seed, scale)
    cache = workdir / "default_metrics.json"
    if cache.exists():
        return json.loads(cache.read_text())
    metrics = _run(baselines.DEFAULT_GREP_PATH, workdir, seed, scale, None)
    cache.write_text(json.dumps(metrics, indent=2) + "\n")
    return metrics


def evaluate_lift(
    program_path: str,
    workdir: Path | str | None = None,
    seed: int = DEFAULT_SEED,
    scale: int = DEFAULT_SCALE,
) -> dict:
    """Score a candidate and report its lift over the default proxy."""
    candidate = evaluate(program_path, workdir, seed, scale)
    default = evaluate_default(workdir, seed, scale)
    return {
        "candidate": candidate,
        "default": default,
        "lift": compute_lift(candidate, default),
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("program", help="Path to a search_tool.py candidate")
    parser.add_argument("--workdir", type=Path, default=None)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--scale", type=int, default=DEFAULT_SCALE)
    parser.add_argument(
        "--lift",
        action="store_true",
        help="also score the default-behavior proxy and report lift",
    )
    args = parser.parse_args()
    fn = evaluate_lift if args.lift else evaluate
    print(json.dumps(fn(args.program, args.workdir, args.seed, args.scale),
                     indent=2))
