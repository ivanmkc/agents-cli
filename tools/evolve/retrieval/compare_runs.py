"""Compare retrieval behavior between two benchmark runs (e.g. pre/post skills).

Mines both run directories with the current miner and reports, per
harness x category x motivation: episode count, steps, tokens — plus
per-transcript normalization and the skill-injection token bill (the
reference payloads skill activations push into context, which episode
mining deliberately does not count as retrieval; a fair pre/post-skills
comparison must show retrieval savings NEXT TO skill spend).

Categories here are analytics buckets over raw episodes (a superset of
the eval-set families — they include buckets with no replay corpus yet):

* ``adk-sdk``        — reverse-engineering the installed google-adk
* ``tool-internals`` — reading the CLI tool's own installed source
* ``skill-docs``     — reading installed skill/reference files (this is
  skills WORKING, not search to eliminate)
* ``cli-help``       — --help / help-text lookups
* ``env-discovery``  — pip show / which / --version probing
* ``log-inspection`` — reading agent/server logs
* ``workspace``      — orienting in the agent's own scaffolded project
* ``other``          — none of the above

Usage::

    python -m evolve.retrieval.compare_runs RUN_A RUN_B \
        --label-a pre-skills --label-b post-skills --out report.json
"""

from __future__ import annotations

import gzip
import json
from collections import Counter
from pathlib import Path

try:
    from evolve.retrieval.log_mining import (
        iter_agent_event_streams,
        mine_run_dir,
        skill_injection_tokens,
    )
except ImportError:  # invoked standalone (python -m from tools/)
    from .log_mining import (
        iter_agent_event_streams,
        mine_run_dir,
        skill_injection_tokens,
    )

CATEGORY_ADK_SDK = "adk-sdk"
CATEGORY_TOOL_INTERNALS = "tool-internals"
CATEGORY_SKILL_DOCS = "skill-docs"
CATEGORY_CLI_HELP = "cli-help"
CATEGORY_ENV = "env-discovery"
CATEGORY_LOG = "log-inspection"
CATEGORY_WORKSPACE = "workspace"
CATEGORY_OTHER = "other"

_SKILL_DIR_MARKERS = (".claude/skills", ".gemini/extensions", ".agents/skills", ".gemini/antigravity-cli/skills")
_CLI_SRC_MARKERS = ("google/agents/cli",)
_ADK_MARKERS = ("google/adk",)
_LOG_MARKERS = ("agents_log", "agent.latest.log", ".log")
_ENV_MARKERS = ("pip show", "pip list", "pip freeze", "which ", "--version")
_WORKSPACE_MARKERS = (
    "agent-project", "app/agent.py", "pyproject.toml", "tests/", "README",
)


def categorize_episode(episode: dict) -> str:
    """Analytics bucket for one mined episode (first matching rule wins)."""
    joined = " ".join(arg for _, arg in episode.get("calls") or [])
    if any(m in joined for m in _CLI_SRC_MARKERS):
        return CATEGORY_TOOL_INTERNALS
    if any(m in joined for m in _ADK_MARKERS) or "site-packages" in joined:
        return CATEGORY_ADK_SDK
    if any(m in joined for m in _SKILL_DIR_MARKERS):
        return CATEGORY_SKILL_DOCS
    if "--help" in joined or "agents-cli help" in joined:
        return CATEGORY_CLI_HELP
    if any(m in joined for m in _ENV_MARKERS):
        return CATEGORY_ENV
    if any(m in joined for m in _LOG_MARKERS):
        return CATEGORY_LOG
    if any(m in joined for m in _WORKSPACE_MARKERS):
        return CATEGORY_WORKSPACE
    return CATEGORY_OTHER


def harness_of(generator: str) -> str:
    """Short harness label from an answer-generator name."""
    generator = generator.lower()
    for name in ("claude", "gemini", "antigravity"):
        if name in generator:
            return name
    return generator or "unknown"


def aggregate(episodes: list[dict], num_transcripts: dict[str, int]) -> dict:
    """(harness, category, motivation) -> {episodes, steps, tokens, per_transcript}."""
    table: dict[tuple, dict] = {}
    for ep in episodes:
        key = (
            harness_of(ep.get("generator", "")),
            categorize_episode(ep),
            ep.get("motivation", "unknown"),
        )
        row = table.setdefault(
            key, {"episodes": 0, "steps": 0, "tokens": 0}
        )
        row["episodes"] += 1
        row["steps"] += ep.get("steps", 0)
        row["tokens"] += ep.get("tokens", 0)
    for (harness, _, _), row in table.items():
        n = num_transcripts.get(harness, 0)
        row["episodes_per_transcript"] = (
            round(row["episodes"] / n, 3) if n else None
        )
    return table


def _load_run_metadata(run_dir: Path) -> dict | None:
    meta_path = run_dir / "run_metadata.json"
    if not meta_path.exists():
        return None
    try:
        return json.loads(meta_path.read_text())
    except (OSError, ValueError):
        return None


def _detect_partial(run_dir: Path, metadata: dict | None) -> bool:
    if metadata is None:
        return False
    expected = metadata.get("num_cases") or metadata.get("expected_cases")
    if expected is None:
        return False
    detail_dir = run_dir / "results_detail"
    actual = len(list(detail_dir.glob("*.json.gz"))) if detail_dir.is_dir() else 0
    return actual != expected


def analyze_run(run_dir: Path | str, min_steps: int = 2) -> dict:
    """Mine one run and aggregate everything the comparison needs."""
    run_dir = Path(run_dir)
    episodes = mine_run_dir(run_dir, min_steps=min_steps)
    metadata = _load_run_metadata(run_dir)

    num_transcripts: Counter = Counter()
    skill_tokens: Counter = Counter()
    for rec_path in sorted((run_dir / "results_detail").glob("*.json.gz")):
        with gzip.open(rec_path, "rt", encoding="utf-8", errors="replace") as fh:
            record = json.load(fh)
        harness = harness_of(record.get("answer_generator") or "")
        num_transcripts[harness] += 1
        for _, events in iter_agent_event_streams(record):
            skill_tokens[harness] += skill_injection_tokens(events)

    result: dict = {
        "run": run_dir.name,
        "num_transcripts": dict(num_transcripts),
        "num_episodes": len(episodes),
        "skill_injection_tokens": dict(skill_tokens),
        "table": {
            "|".join(k): v
            for k, v in sorted(
                aggregate(episodes, dict(num_transcripts)).items()
            )
        },
    }
    if metadata is not None:
        result["run_metadata"] = metadata
    if _detect_partial(run_dir, metadata):
        result["partial"] = True
    return result


def _extract_models(side_a: dict, side_b: dict, label_a: str, label_b: str) -> dict:
    """Build {label: {harness: model_id}} from run_metadata if present."""
    out = {}
    for label, side in [(label_a, side_a), (label_b, side_b)]:
        meta = side.get("run_metadata") or {}
        models = meta.get("models") or {}
        if models:
            out[label] = models
        else:
            harnesses = list(side.get("num_transcripts", {}).keys())
            out[label] = {h: meta.get("model", "unknown") for h in harnesses}
    return out


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_a", type=Path)
    parser.add_argument("run_b", type=Path)
    parser.add_argument("--label-a", default="run-a")
    parser.add_argument("--label-b", default="run-b")
    parser.add_argument("--min-steps", type=int, default=2)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    side_a = analyze_run(args.run_a, args.min_steps)
    side_b = analyze_run(args.run_b, args.min_steps)
    report: dict = {
        args.label_a: side_a,
        args.label_b: side_b,
    }

    report["models"] = _extract_models(side_a, side_b, args.label_a, args.label_b)
    same_model = report["models"].get(args.label_a) == report["models"].get(args.label_b)
    report["same_model_baseline"] = same_model
    report["methodology_note"] = (
        "N=1 per condition; differences are descriptive, not causal"
    )
    if not same_model:
        report["confounds"] = [
            "model differs between runs",
            "any behavioral difference may be model-driven, not treatment-driven",
        ]

    cats_a = {k.split("|")[1] for k in side_a["table"]}
    cats_b = {k.split("|")[1] for k in side_b["table"]}
    report["comparable_categories"] = sorted(cats_a & cats_b)

    text = json.dumps(report, indent=1)
    if args.out:
        args.out.write_text(text)
        print(f"report -> {args.out}")
    for label, side in [(args.label_a, side_a), (args.label_b, side_b)]:
        partial = " [PARTIAL]" if side.get("partial") else ""
        print(f"\n== {label}: {side['run']}{partial} ==")
        print(f"transcripts={side['num_transcripts']} "
              f"episodes={side['num_episodes']} "
              f"skill_injection_tokens={side['skill_injection_tokens']}")
        for key, row in side["table"].items():
            print(f"  {key:55s} eps={row['episodes']:4d} "
                  f"tok={row['tokens']:8,d} "
                  f"eps/transcript={row['episodes_per_transcript']}")


if __name__ == "__main__":
    main()
