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
import sys
import tempfile
from pathlib import Path

try:
    from evolve.retrieval.evaluator import LocalEvaluator
    from evolve.retrieval.monorepo import generate_monorepo
except ImportError:  # invoked standalone by an evolution runner
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from evolve.retrieval.evaluator import LocalEvaluator
    from evolve.retrieval.monorepo import generate_monorepo

DEFAULT_SEED = 0
DEFAULT_SCALE = 3
_STAGE1_TASKS = 20


def _default_workdir(seed: int, scale: int) -> Path:
    override = os.environ.get("EVOLVE_RETRIEVAL_WORKDIR")
    if override:
        return Path(override)
    return (
        Path(tempfile.gettempdir())
        / f"agents-cli-evolve-retrieval-s{seed}-x{scale}"
    )


def _ensure_benchmark(workdir: Path, seed: int, scale: int) -> tuple[Path, dict]:
    """Build the mock monorepo once; reuse it on every later call."""
    workdir.mkdir(parents=True, exist_ok=True)
    repo = workdir / "repo"
    manifest_path = workdir / "repo.tasks.json"
    if manifest_path.exists():
        return repo, json.loads(manifest_path.read_text())
    return repo, generate_monorepo(repo, seed=seed, scale=scale)


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
        manifest = dict(manifest, tasks=manifest["tasks"][:n_tasks])
    return LocalEvaluator(repo, manifest).evaluate_program(program_path)


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


def evaluate(
    program_path: str,
    workdir: Path | str | None = None,
    seed: int = DEFAULT_SEED,
    scale: int = DEFAULT_SCALE,
) -> dict:
    """Main AlphaEvolve fitness function (same as stage 2)."""
    return evaluate_stage2(program_path, workdir, seed, scale)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("program", help="Path to a search_tool.py candidate")
    parser.add_argument("--workdir", type=Path, default=None)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--scale", type=int, default=DEFAULT_SCALE)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.program, args.workdir, args.seed,
                              args.scale), indent=2))
