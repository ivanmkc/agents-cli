"""Replay the real-log eval set and measure retrieval cost.

For every case in a ``real_eval`` manifest, runs a candidate search
tool (same CLI contract as ``search_tool.py``), measures wall-clock
latency per query, scores relevance with ``LocalEvaluator``'s verified
chunk scoring, and reports the three costs the mined episodes recorded
for the real agent:

* **steps** — a search tool answers in 1 call; the agent's observed
  median was several greps/reads.
* **tokens** — verified tokens the tool returns vs. the chars/4 the
  agent's retrieval detour actually dragged into context.
* **wall seconds** — subprocess latency vs. the episode's span.

Usage::

    python -m evolve.retrieval.real_replay manifest.json \
        --sdk-root .../site-packages/google/adk \
        --project-root .../agent-project \
        [--tool path/to/tool.py ...] [--out report.json]

Without ``--tool`` it runs the two built-in reference points:
``baselines/default_grep.py`` (today's grep-then-read-whole-files
behavior) and ``search_tool.py`` (the evolved retrieval baseline).
"""

from __future__ import annotations

import json
import statistics
import tempfile
import time
from pathlib import Path

try:
    from evolve.retrieval.baselines import DEFAULT_GREP_PATH
    from evolve.retrieval.evaluator import LocalEvaluator
except ImportError:  # invoked standalone
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from evolve.retrieval.baselines import DEFAULT_GREP_PATH
    from evolve.retrieval.evaluator import LocalEvaluator

SEARCH_TOOL_PATH = str(Path(__file__).resolve().parent / "search_tool.py")


def _median(values: list) -> float:
    values = [v for v in values if v is not None]
    return round(statistics.median(values), 3) if values else 0.0


def _mean(values: list) -> float:
    values = [v for v in values if v is not None]
    return round(statistics.mean(values), 3) if values else 0.0


def observed_summary(tasks: list[dict]) -> dict:
    """The real agent's measured retrieval cost across these cases."""
    steps = [t["observed"]["steps"] for t in tasks]
    tokens = [t["observed"]["tokens"] for t in tasks]
    wall = [
        t["observed"]["wall_seconds"]
        for t in tasks
        # Some harnesses log no per-event timestamps; a 0-second span is
        # missing data, not an instant search.
        if t["observed"]["wall_seconds"]
    ]
    return {
        "num_cases": len(tasks),
        "steps_median": _median(steps),
        "steps_mean": _mean(steps),
        "tokens_median": _median(tokens),
        "tokens_mean": _mean(tokens),
        "wall_seconds_median": _median(wall),
        "wall_seconds_mean": _mean(wall),
        "wall_seconds_n": len(wall),
    }


def replay_tool(
    tool_path: str,
    tasks_by_corpus: dict[str, tuple[Path, list[dict]]],
    timeout: float = 20.0,
) -> dict:
    """Run one tool over every case; per-family and overall metrics."""
    per_case: list[dict] = []
    for corpus, (root, tasks) in tasks_by_corpus.items():
        evaluator = LocalEvaluator(root, {"tasks": tasks}, timeout=timeout)
        with tempfile.TemporaryDirectory(prefix="real-replay-") as sandbox:
            for task in tasks:
                t0 = time.monotonic()
                result = evaluator._run_task(tool_path, task, sandbox)
                result["wall_seconds"] = round(time.monotonic() - t0, 3)
                result["family"] = task["family"]
                result["corpus"] = corpus
                per_case.append(result)

    def bucket(results: list[dict]) -> dict:
        n = len(results) or 1
        return {
            "num_cases": len(results),
            "failures": sum(1 for r in results if r["failed"]),
            "recall": round(sum(r["recall"] for r in results) / n, 4),
            "precision": round(sum(r["precision"] for r in results) / n, 4),
            "mrr": round(sum(r["rr"] for r in results) / n, 4),
            "steps_median": 1,
            "tokens_median": _median([r["tokens"] for r in results]),
            "tokens_mean": _mean([r["tokens"] for r in results]),
            "wall_seconds_median": _median(
                [r["wall_seconds"] for r in results]
            ),
            "wall_seconds_mean": _mean([r["wall_seconds"] for r in results]),
        }

    families = sorted({r["family"] for r in per_case})
    return {
        "overall": bucket(per_case),
        "by_family": {
            fam: bucket([r for r in per_case if r["family"] == fam])
            for fam in families
        },
    }


def compare(tool_metrics: dict, observed: dict) -> dict:
    """Cost reduction of a 1-shot tool vs. the agent's real detour."""
    out = {}
    for key in ("steps", "tokens"):
        agent = observed[f"{key}_median"]
        tool = tool_metrics[f"{key}_median"]
        out[f"{key}_reduction_pct"] = (
            round((agent - tool) / agent * 100, 1) if agent else 0.0
        )
    agent_wall = observed["wall_seconds_median"]
    tool_wall = tool_metrics["wall_seconds_median"]
    out["wall_seconds_reduction_pct"] = (
        round((agent_wall - tool_wall) / agent_wall * 100, 1)
        if agent_wall
        else 0.0
    )
    return out


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--sdk-root", type=Path, default=None)
    parser.add_argument("--project-root", type=Path, default=None)
    parser.add_argument(
        "--tool", action="append", type=Path, default=None,
        help="candidate tool(s); default: default_grep + search_tool",
    )
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text())
    tasks = manifest["tasks"]

    roots = {"sdk": args.sdk_root, "project": args.project_root}
    tasks_by_corpus: dict[str, tuple[Path, list[dict]]] = {}
    skipped = 0
    for corpus, root in roots.items():
        subset = [t for t in tasks if t["corpus"] == corpus]
        if root is None:
            skipped += len(subset)
            continue
        if subset:
            tasks_by_corpus[corpus] = (root, subset)
    if skipped:
        print(f"note: {skipped} cases skipped (corpus root not given)")

    scored = [t for _, ts in tasks_by_corpus.values() for t in ts]
    observed_all = observed_summary(scored)
    observed_by_family = {
        fam: observed_summary([t for t in scored if t["family"] == fam])
        for fam in sorted({t["family"] for t in scored})
    }

    tools = [str(p) for p in (args.tool or [])] or [
        DEFAULT_GREP_PATH,
        SEARCH_TOOL_PATH,
    ]
    report = {
        "num_cases": len(scored),
        "observed_agent": {
            "overall": observed_all,
            "by_family": observed_by_family,
        },
        "tools": {},
    }
    for tool_path in tools:
        name = Path(tool_path).stem
        metrics = replay_tool(tool_path, tasks_by_corpus, args.timeout)
        metrics["vs_observed"] = compare(metrics["overall"], observed_all)
        report["tools"][name] = metrics
        o = metrics["overall"]
        v = metrics["vs_observed"]
        print(
            f"{name}: recall={o['recall']} precision={o['precision']} "
            f"mrr={o['mrr']} tokens_med={o['tokens_median']} "
            f"wall_med={o['wall_seconds_median']}s "
            f"| vs agent: steps {v['steps_reduction_pct']}% "
            f"tokens {v['tokens_reduction_pct']}% "
            f"time {v['wall_seconds_reduction_pct']}%"
        )

    if args.out:
        args.out.write_text(json.dumps(report, indent=2) + "\n")
        print(f"report -> {args.out}")


if __name__ == "__main__":
    main()
