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

import json
import os
import random
import sys
import tempfile
from pathlib import Path

try:
    from evolve.retrieval import baselines
    from evolve.retrieval.evaluator import LocalEvaluator, compute_lift
    from evolve.retrieval.monorepo import generate_monorepo
except ImportError:  # invoked standalone by an evolution runner
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from evolve.retrieval import baselines
    from evolve.retrieval.evaluator import LocalEvaluator, compute_lift
    from evolve.retrieval.monorepo import generate_monorepo

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
_REAL_ROOT_ENV = {
    "project": "EVOLVE_REAL_PROJECT_ROOT",
    "sdk": "EVOLVE_REAL_SDK_ROOT",
}


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
) -> dict:
    """Score a candidate against the validated real-log eval cases.

    ``corpus_roots`` maps corpus name (``project`` / ``sdk``) to the
    directory the tasks' spans refer to; unset corpora fall back to the
    ``EVOLVE_REAL_PROJECT_ROOT`` / ``EVOLVE_REAL_SDK_ROOT`` env vars.
    Tasks whose corpus has no root are skipped and counted in
    ``num_skipped`` — metrics are the task-weighted mean of the rest.
    """
    manifest_path = Path(
        manifest_path
        or os.environ.get("EVOLVE_REAL_MANIFEST")
        or _REAL_MANIFEST_DEFAULT
    )
    tasks = json.loads(manifest_path.read_text())["tasks"]
    if corpus_roots is not None:
        roots = {k: Path(v) for k, v in corpus_roots.items()}
    else:
        roots = {
            corpus: Path(value)
            for corpus, var in _REAL_ROOT_ENV.items()
            if (value := os.environ.get(var))
        }
    per_corpus: dict[str, dict] = {}
    totals = {"combined_score": 0.0, "recall": 0.0, "precision": 0.0, "mrr": 0.0}
    num_tasks = 0
    for corpus, root in sorted(roots.items()):
        subset = [t for t in tasks if t.get("corpus") == corpus]
        if not subset:
            continue
        metrics = LocalEvaluator(root, {"tasks": subset}).evaluate_program(
            program_path
        )
        per_corpus[corpus] = metrics
        n = metrics["num_tasks"]
        num_tasks += n
        for key in totals:
            totals[key] += metrics.get(key, 0.0) * n
    if num_tasks:
        for key in totals:
            totals[key] = round(totals[key] / num_tasks, 4)
    return {
        "num_tasks": num_tasks,
        "num_skipped": len(tasks) - num_tasks,
        "per_corpus": per_corpus,
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
