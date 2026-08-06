"""Mine retrieval episodes from agent-generator benchmark transcripts.

The token-economics analysis of the golden/comparison lift runs showed
that baseline agents spend a large share of their turns *searching* —
probing the environment, reverse-engineering the SDK from site-packages,
grepping their own half-built project. Each of those searches is a real,
measured retrieval workload: what the agent wanted to know, how many
steps it took, how long it took, and how many tokens it cost.

This module extracts those workloads so they can be replayed against a
retrieval tool and compared with the synthetic mock-monorepo benchmark.

Input format (agent-generator ``results_detail/*.json.gz``)::

    record["generation_attempts"][i]["generation_events"]["turns"][j]
          ["agent_events"] -> [{type, timestamp, tool_name, tool_input,
                                tool_output, duration_ms, ...}, ...]

An *episode* is a maximal run of consecutive retrieval-class tool calls
(reads / greps / globs / ls / read-only shell), tolerating interleaved
assistant messages, ended by an action-class call (edit / write /
install / run). Cost conventions match the book: tokens are chars/4,
wall time comes from event timestamps.
"""

from __future__ import annotations

import gzip
import json
import re
import shlex
from pathlib import Path

# Tool-name taxonomy across the three harnesses observed in the runs
# (Claude / Gemini / AGY — see the token-economics book, Ch. 6.5).
_RETRIEVAL_TOOLS = {
    # Claude
    "Read", "Grep", "Glob", "LS", "WebFetch", "WebSearch",
    # Gemini
    "read_file", "grep_search", "glob", "list_directory", "search_web",
    "web_fetch", "google_web_search", "read_many_files",
    # AGY
    "list_dir", "view_file", "codebase_search",
}
_SHELL_TOOLS = {"Bash", "run_shell_command", "run_command", "shell"}
_ACTION_TOOLS = {
    "Edit", "Write", "MultiEdit", "NotebookEdit",
    "write_file", "replace", "edit_file",
    "write_to_file", "replace_file_content",
}
_NEUTRAL_TOOLS = {"TodoWrite", "write_todos", "task_boss", "ExitPlanMode"}

# Read-only leading programs: running one of these is information
# seeking, not action. `pip show/list` and `which` are env discovery;
# `cat/head/grep/find` on site-packages is SDK reverse-engineering.
_READONLY_PROGRAMS = {
    "cat", "head", "tail", "less", "more", "ls", "dir", "tree", "wc",
    "grep", "egrep", "fgrep", "rg", "ag", "find", "fd", "locate",
    "which", "whereis", "type", "file", "stat", "du", "df", "pwd",
    "printenv", "env", "echo", "sed -n",
}
_READONLY_PATTERNS = [
    re.compile(r"^pip3?\s+(show|list|freeze|index)\b"),
    re.compile(r"^python3?\s+-c\s+.{0,120}\b(import|print|__version__|__file__)"),
    re.compile(r"^uv\s+pip\s+(show|list|freeze)\b"),
    re.compile(r"--help\s*$"),
    re.compile(r"^man\s"),
]


def _ts(value) -> float:
    """Timestamp as epoch seconds; handles floats and ISO-8601 strings."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value:
        from datetime import datetime
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0
    return 0.0


def _leading_program(command: str) -> str:
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    for part in parts:
        if "=" in part and not part.startswith(("/", ".")):
            continue  # skip VAR=val prefixes
        if part in ("sudo", "command", "builtin", "nohup", "timeout"):
            continue
        return part.rsplit("/", 1)[-1]
    return ""


def _shell_is_readonly(command: str) -> bool:
    command = command.strip()
    # Pipelines / sequences: every stage must be read-only.
    stages = re.split(r"\||&&|;", command)
    ok = 0
    for stage in stages:
        stage = stage.strip()
        if not stage:
            continue
        prog = _leading_program(stage)
        if prog in _READONLY_PROGRAMS:
            ok += 1
            continue
        if any(p.search(stage) for p in _READONLY_PATTERNS):
            ok += 1
            continue
        return False
    return ok > 0


def classify_tool_call(tool_name: str, tool_input: dict | str | None) -> str:
    """'retrieval' | 'action' | 'neutral' for one tool_use event."""
    if tool_name in _RETRIEVAL_TOOLS:
        return "retrieval"
    if tool_name in _ACTION_TOOLS:
        return "action"
    if tool_name in _NEUTRAL_TOOLS:
        return "neutral"
    if tool_name in _SHELL_TOOLS:
        if isinstance(tool_input, dict):
            command = str(
                tool_input.get("command") or tool_input.get("cmd") or ""
            )
        else:
            command = str(tool_input or "")
        return "retrieval" if _shell_is_readonly(command) else "action"
    return "neutral"


def _size(value) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value)
    try:
        return len(json.dumps(value))
    except (TypeError, ValueError):
        return len(str(value))


_FILE_INPUT_KEYS = {
    "file_path", "path", "absolute_path", "absolutepath",
    "target_file", "targetfile", "filename",
}


def _input_paths(tool_input) -> list[str]:
    """File paths named in a tool_use input, if any."""
    if not isinstance(tool_input, dict):
        return []
    return [
        str(v).strip()
        for k, v in tool_input.items()
        if k.lower() in _FILE_INPUT_KEYS and isinstance(v, str) and v.strip()
    ]


def _same_file(a: str, b: str) -> bool:
    """True when two paths plausibly name the same file (rel vs abs)."""
    return a == b or a.endswith("/" + b) or b.endswith("/" + a)


def _is_verification_read(tool_name: str, tool_input, written: set[str]) -> bool:
    """A retrieval call that only revisits files the agent itself wrote."""
    if not written:
        return False
    if tool_name in _SHELL_TOOLS:
        command = str(
            tool_input.get("command") or tool_input.get("cmd") or ""
            if isinstance(tool_input, dict) else tool_input or ""
        )
        return any(w in command for w in written)
    paths = _input_paths(tool_input)
    return bool(paths) and all(
        any(_same_file(p, w) for w in written) for p in paths
    )


def _input_summary(tool_name: str, tool_input) -> str:
    if isinstance(tool_input, dict):
        for key in ("command", "cmd", "file_path", "path", "pattern",
                    "query", "absolute_path"):
            if tool_input.get(key):
                return str(tool_input[key])[:300]
        return json.dumps(tool_input)[:300]
    return str(tool_input)[:300]


def segment_episodes(events: list[dict], min_steps: int = 1) -> list[dict]:
    """Split a flat agent_events list into retrieval episodes.

    Messages between retrieval calls keep the episode alive (agents
    narrate mid-search); an action call ends it; neutral tools are
    ignored. Episodes with fewer than ``min_steps`` retrieval calls are
    dropped (a single routine read before an edit isn't a search).

    Streamed assistant messages arrive as several ``message`` events per
    utterance; consecutive chunks are joined before use as context.
    Retrieval calls that only revisit files the agent itself wrote
    earlier in the stream are verification reads, not search, and are
    skipped.
    """
    episodes: list[dict] = []
    current: dict | None = None
    last_message = ""
    pending_chunks: list[str] = []
    written: set[str] = set()

    # Pair tool_results back onto their tool_use by call id.
    outputs: dict[str, int] = {}
    result_ts: dict[str, float] = {}
    for ev in events:
        if ev.get("type") == "tool_result":
            cid = ev.get("tool_call_id")
            if cid:
                outputs[cid] = _size(ev.get("tool_output"))
                ts = _ts(ev.get("timestamp"))
                if ts:
                    result_ts[cid] = ts

    def close(ended_by: str | None):
        nonlocal current
        if current and current["steps"] >= min_steps:
            current["ended_by"] = ended_by
            current["tokens"] = max(1, current.pop("_chars") // 4)
            current["wall_seconds"] = round(
                max(0.0, current.pop("_t_last") - current.pop("_t_first")), 3
            )
            episodes.append(current)
        current = None

    for ev in events:
        etype = ev.get("type")
        if etype == "message":
            content = ev.get("content")
            if isinstance(content, str) and content.strip():
                pending_chunks.append(content)
            continue
        if etype != "tool_use":
            continue
        tool = str(ev.get("tool_name") or "")
        tool_input = ev.get("tool_input")
        kind = classify_tool_call(tool, tool_input)
        if kind == "neutral":
            continue
        if pending_chunks:
            last_message = "".join(pending_chunks)
            pending_chunks.clear()
        if kind == "action":
            written.update(_input_paths(tool_input))
            close(ended_by=tool)
            continue
        # retrieval
        if _is_verification_read(tool, tool_input, written):
            continue
        ts = _ts(ev.get("timestamp"))
        cid = str(ev.get("tool_call_id") or "")
        out_chars = outputs.get(cid, 0)
        end_ts = max(ts, result_ts.get(cid, ts))
        if current is None:
            current = {
                "context": last_message[:500],
                "calls": [],
                "steps": 0,
                "_chars": 0,
                "_t_first": ts,
                "_t_last": end_ts,
            }
        current["calls"].append((tool, _input_summary(tool, tool_input)))
        current["steps"] += 1
        current["_chars"] += _size(tool_input) + out_chars
        current["_t_last"] = max(current["_t_last"], end_ts)
    close(ended_by=None)
    return episodes


def iter_agent_event_streams(record: dict):
    """Yield (label, events) for every agent_events list in a record."""
    for i, attempt in enumerate(record.get("generation_attempts") or []):
        ge = attempt.get("generation_events") or {}
        for j, turn in enumerate(ge.get("turns") or []):
            evs = turn.get("agent_events") or []
            if evs:
                yield f"gen{i}.turn{j}", evs
    sim = record.get("simulation_context") or {}
    for i, conv in enumerate(sim.get("conversation_events") or []):
        for j, turn in enumerate((conv or {}).get("turns") or []):
            evs = turn.get("agent_events") or []
            if evs:
                yield f"sim{i}.turn{j}", evs


def mine_record(path: Path | str, min_steps: int = 2) -> list[dict]:
    """All retrieval episodes in one results_detail record."""
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
        record = json.load(fh)
    case_id = record.get("id") or path.stem
    generator = record.get("answer_generator") or ""
    out = []
    for label, events in iter_agent_event_streams(record):
        for ep in segment_episodes(events, min_steps=min_steps):
            ep["case"] = case_id
            ep["generator"] = generator
            ep["stream"] = label
            out.append(ep)
    return out


def mine_run_dir(run_dir: Path | str, min_steps: int = 2) -> list[dict]:
    """All retrieval episodes across every case record in a run."""
    run_dir = Path(run_dir)
    out = []
    for rec in sorted((run_dir / "results_detail").glob("*.json.gz")):
        try:
            episodes = mine_record(rec, min_steps=min_steps)
        except (OSError, ValueError, KeyError):
            continue
        for ep in episodes:
            ep["run"] = run_dir.name
        out.extend(episodes)
    return out


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dirs", nargs="+", type=Path)
    parser.add_argument("--min-steps", type=int, default=2)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    all_eps = []
    for rd in args.run_dirs:
        all_eps.extend(mine_run_dir(rd, min_steps=args.min_steps))
    text = "\n".join(json.dumps(ep, default=list) for ep in all_eps)
    if args.out:
        args.out.write_text(text + "\n")
        print(f"{len(all_eps)} episodes -> {args.out}")
    else:
        print(text)
