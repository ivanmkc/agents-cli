"""Adapter to the official AlphaEvolve client evaluation contract.

The public AlphaEvolve client library (see the Google Cloud codelabs and
github.com/Google-Cloud-AI/alphaevolve-on-googlecloud) calls a
user-supplied evaluation function with a ``program_candidate`` dict and
expects an ``AlphaEvolveProgramEvaluation``-shaped payload back::

    candidate["content"]["files"][0]["content"]   # mutated source code
    -> {"scores":   {"scores":   [{"metric": str, "score": float}]},
        "insights": {"insights": [{"label": str, "text": str}]}}

This module bridges that contract onto the LocalEvaluator: the candidate
code is written to a temp file and executed as a sandboxed subprocess per
benchmark task (hard timeouts), which is stricter than the codelab's
in-process ``exec()`` — an LLM-mutated search tool that hangs or crashes
costs one task's score, never the controller loop.

All reported metrics are maximization-friendly (token cost is reported
as ``neg_avg_tokens``), and insights carry the qualitative feedback that
steers the next round of mutations: runtime failures, token bloat, and
the weakest task kind.

The payload is built as plain dicts (matching
``AlphaEvolveProgramEvaluation.model_dump()``) so this module works
without the ``alpha_evolve`` package installed — only
``run_evolution.py`` needs it.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

try:
    from evolve.retrieval import evaluate as evaluate_mod
except ImportError:  # invoked standalone by an evolution runner
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from evolve.retrieval import evaluate as evaluate_mod

PRIMARY_METRIC = "combined_score"

# The API requires a numeric score per metric and maximizes it; a large
# negative sentinel keeps malformed candidates from ever being selected.
SENTINEL_SCORE = -1e12


def _payload(scores: dict[str, float], insights: list[dict]) -> dict:
    result: dict = {
        "scores": {
            "scores": [
                {"metric": metric, "score": float(score)}
                for metric, score in scores.items()
            ]
        }
    }
    if insights:
        result["insights"] = {"insights": insights}
    return result


def _insights_from_metrics(metrics: dict) -> list[dict]:
    insights: list[dict] = []
    if metrics["failures"]:
        insights.append(
            {
                "label": "Runtime failures",
                "text": (
                    f"{metrics['failures']} of {metrics['num_tasks']} benchmark "
                    "tasks failed (crash, timeout, or malformed JSON output). "
                    "Each failed task scores zero."
                ),
            }
        )
    if metrics["precision"] < 0.5:
        insights.append(
            {
                "label": "Token bloat",
                "text": (
                    f"Only {metrics['precision']:.0%} of returned tokens were "
                    f"relevant (avg {metrics['avg_tokens_returned']:.0f} tokens "
                    "per call). Return tighter, block-level chunks instead of "
                    "large regions."
                ),
            }
        )
    by_kind = metrics.get("by_kind") or {}
    if by_kind:
        weakest = min(by_kind, key=lambda k: by_kind[k]["recall"])
        insights.append(
            {
                "label": f"Weakest task kind: {weakest}",
                "text": (
                    f"Recall by task kind: "
                    + ", ".join(
                        f"{kind}={stats['recall']:.2f}"
                        for kind, stats in sorted(by_kind.items())
                    )
                    + f". '{weakest}' queries need the most improvement."
                ),
            }
        )
    if metrics["mrr"] < 0.7:
        insights.append(
            {
                "label": "Ranking quality",
                "text": (
                    f"Mean reciprocal rank is {metrics['mrr']:.2f}: relevant "
                    "chunks are found but not ranked first. Improve scoring so "
                    "the best chunk leads."
                ),
            }
        )
    return insights


def retrieval_evaluation(
    program_candidate: dict,
    workdir: Path | str | None = None,
    seed: int = evaluate_mod.DEFAULT_SEED,
    scale: int = evaluate_mod.DEFAULT_SCALE,
) -> dict:
    """Official-contract evaluation function for the retrieval loop.

    Pass this to ``AlphaEvolveExperiment``; use ``functools.partial`` to
    pin a custom workdir/seed/scale.
    """
    try:
        code = program_candidate["content"]["files"][0]["content"]
    except (KeyError, IndexError, TypeError):
        return _payload(
            {PRIMARY_METRIC: SENTINEL_SCORE},
            [
                {
                    "label": "Malformed candidate",
                    "text": (
                        "Expected candidate code at "
                        "content.files[0].content; none was found."
                    ),
                }
            ],
        )

    with tempfile.NamedTemporaryFile(
        "w", suffix="_search_tool.py", delete=False
    ) as handle:
        handle.write(code)
        program_path = handle.name
    try:
        metrics = evaluate_mod.evaluate(program_path, workdir, seed, scale)
    finally:
        Path(program_path).unlink(missing_ok=True)

    scores = {
        PRIMARY_METRIC: metrics["combined_score"],
        "recall": metrics["recall"],
        "precision": metrics["precision"],
        "mrr": metrics["mrr"],
        "neg_avg_tokens": -metrics["avg_tokens_returned"],
    }
    return _payload(scores, _insights_from_metrics(metrics))
