"""Agent-in-the-loop lift measurement: evolved tool vs. agents-cli as-is.

The LocalEvaluator scores search *tools* in isolation. This harness
answers the question that actually matters for shipping: how much does
a real coding agent improve when it retrieves through the search tool
instead of its native grep/read workflow?

Two arms, same benchmark tasks, same mock monorepo, same agent model:

* **default** — the agent solves each retrieval task with whatever
  built-in tools it has (Grep/Glob/Read...). This is agents-cli as-is.
* **tool** — the agent must route all code search through
  ``search_tool.py`` (native search tools are disabled via
  ``--disallowedTools``; the instruction pins the exact command).

Per task we record: correctness (returned file/line span overlaps the
ground truth), total tokens consumed (input + cache + output — the real
context-tax number), agent turns, and cost. The report is the per-arm
aggregate plus the lift.

Runs headless via ``claude -p --output-format json``. Requires the
Claude Code CLI on PATH; each task costs real LLM calls, so sample
sizes are small by default (this is the outer-loop measurement, not the
evolution fitness function).
"""

from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
import sys
from pathlib import Path

try:
    from evolve.retrieval import evaluate as evaluate_mod
except ImportError:  # invoked standalone
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from evolve.retrieval import evaluate as evaluate_mod

_ANSWER_KEYS = {"file", "start_line", "end_line"}

_PROMPT = """\
You are answering a code-retrieval task inside the repository at your
current working directory.

Task: {query}

{tool_instruction}Reply with ONLY a JSON object (no prose, no code fence):
{{"file": "<repo-relative path>", "start_line": <int>, "end_line": <int>}}
identifying the code region that answers the task. If the task asks for
multiple locations, return the single most important one.
"""

_TOOL_INSTRUCTION = """\
IMPORTANT: for ALL code searching you MUST use exactly this command:
    python {tool} --repo . --query "<your search query>"
It prints ranked JSON chunks with file paths and line numbers. Do not
use grep, rg, find, or directory listings; do not read files except to
verify a specific chunk.

"""


def extract_answer(text: str) -> dict | None:
    """Pull the answer JSON out of the agent's (possibly chatty) reply."""
    for match in re.finditer(r"\{[^{}]*\}", text, re.DOTALL):
        try:
            obj = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        if _ANSWER_KEYS <= set(obj):
            try:
                return {
                    "file": str(obj["file"]),
                    "start_line": int(obj["start_line"]),
                    "end_line": int(obj["end_line"]),
                }
            except (TypeError, ValueError):
                continue
    return None


def is_correct(answer: dict | None, spans: list[dict]) -> bool:
    if not answer:
        return False
    return any(
        answer["file"] == span["file"]
        and answer["start_line"] <= span["end_line"]
        and answer["end_line"] >= span["start_line"]
        for span in spans
    )


def sample_tasks(tasks: list[dict], n: int, seed: int = 0) -> list[dict]:
    """Deterministic, kind-stratified sample of benchmark tasks."""
    rng = random.Random(seed)
    by_kind: dict[str, list[dict]] = {}
    for task in tasks:
        by_kind.setdefault(task["kind"], []).append(task)
    picked: list[dict] = []
    kinds = sorted(by_kind)
    quota, extra = divmod(n, len(kinds))
    for i, kind in enumerate(kinds):
        take = quota + (1 if i < extra else 0)
        pool = sorted(by_kind[kind], key=lambda t: t["id"])
        picked.extend(rng.sample(pool, min(take, len(pool))))
    return picked


def _run_agent(
    repo: Path, prompt: str, model: str, max_turns: int, disallowed: str | None
) -> dict:
    cmd = [
        "claude",
        "-p",
        prompt,
        "--output-format",
        "json",
        "--model",
        model,
        "--max-turns",
        str(max_turns),
        "--dangerously-skip-permissions",
    ]
    if disallowed:
        cmd += ["--disallowedTools", disallowed]
    proc = subprocess.run(
        cmd, cwd=repo, capture_output=True, text=True, timeout=600
    )
    if proc.returncode != 0:
        return {"error": proc.stderr.strip()[-500:]}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"error": f"unparseable output: {proc.stdout[-300:]}"}


def _total_tokens(usage: dict) -> int:
    return sum(
        int(usage.get(key, 0) or 0)
        for key in (
            "input_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
            "output_tokens",
        )
    )


def run_arm(
    arm: str,
    repo: Path,
    tasks: list[dict],
    tool_path: str,
    model: str,
    max_turns: int,
) -> dict:
    if arm == "tool":
        tool_instruction = _TOOL_INSTRUCTION.format(tool=tool_path)
        disallowed = "Grep,Glob"
    else:
        tool_instruction = ""
        disallowed = None

    per_task = []
    for task in tasks:
        prompt = _PROMPT.format(
            query=task["query"], tool_instruction=tool_instruction
        )
        result = _run_agent(repo, prompt, model, max_turns, disallowed)
        usage = result.get("usage") or {}
        answer = extract_answer(result.get("result") or "")
        record = {
            "task": task["id"],
            "correct": is_correct(answer, task["expected_spans"]),
            "tokens": _total_tokens(usage),
            "turns": int(result.get("num_turns") or 0),
            "cost_usd": float(result.get("total_cost_usd") or 0.0),
            "error": result.get("error"),
        }
        per_task.append(record)
        print(
            f"  [{arm}] {task['id']}: correct={record['correct']} "
            f"tokens={record['tokens']} turns={record['turns']}",
            file=sys.stderr,
        )

    n = len(per_task) or 1
    return {
        "arm": arm,
        "accuracy": round(sum(r["correct"] for r in per_task) / n, 3),
        "mean_tokens": round(sum(r["tokens"] for r in per_task) / n, 1),
        "mean_turns": round(sum(r["turns"] for r in per_task) / n, 2),
        "total_cost_usd": round(sum(r["cost_usd"] for r in per_task), 4),
        "errors": sum(1 for r in per_task if r["error"]),
        "per_task": per_task,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", type=Path, default=None)
    parser.add_argument("--tool", type=Path, default=None,
                        help="search tool to measure (default: bundled seed)")
    parser.add_argument("--tasks", type=int, default=9)
    parser.add_argument("--sample-seed", type=int, default=0)
    parser.add_argument("--model", default="haiku")
    parser.add_argument("--max-turns", type=int, default=12)
    parser.add_argument("--arms", default="default,tool")
    parser.add_argument("--seed", type=int, default=evaluate_mod.DEFAULT_SEED)
    parser.add_argument("--scale", type=int, default=evaluate_mod.DEFAULT_SCALE)
    args = parser.parse_args(argv)

    workdir = args.workdir or evaluate_mod._default_workdir(args.seed, args.scale)
    repo, manifest = evaluate_mod._ensure_benchmark(
        Path(workdir), args.seed, args.scale
    )
    tool_path = str(
        (args.tool or Path(__file__).with_name("search_tool.py")).resolve()
    )
    tasks = sample_tasks(manifest["tasks"], args.tasks, args.sample_seed)

    report: dict = {"model": args.model, "n_tasks": len(tasks), "arms": {}}
    for arm in args.arms.split(","):
        print(f"Running arm '{arm}' on {len(tasks)} tasks...", file=sys.stderr)
        report["arms"][arm] = run_arm(
            arm, repo, tasks, tool_path, args.model, args.max_turns
        )

    if {"default", "tool"} <= set(report["arms"]):
        default, tool = report["arms"]["default"], report["arms"]["tool"]
        report["lift"] = {
            "accuracy_delta": round(tool["accuracy"] - default["accuracy"], 3),
            "token_reduction_pct": (
                round(
                    (default["mean_tokens"] - tool["mean_tokens"])
                    / default["mean_tokens"]
                    * 100,
                    1,
                )
                if default["mean_tokens"]
                else 0.0
            ),
            "turn_reduction_pct": (
                round(
                    (default["mean_turns"] - tool["mean_turns"])
                    / default["mean_turns"]
                    * 100,
                    1,
                )
                if default["mean_turns"]
                else 0.0
            ),
        }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
