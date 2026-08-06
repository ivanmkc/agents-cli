"""Programmatic proxy for today's default agent retrieval behavior.

Emulates the grep -> read-the-whole-file workflow agents fall back on
when no dedicated search tool exists: find files mentioning the query
terms, rank by match count, and return ENTIRE files. Deliberately
ignores the token budget — so does real life.

Same CLI contract as search_tool.py, so LocalEvaluator can score it and
compute_lift can report the gap. This file is a fixed reference: it is
never evolved.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="grep+read baseline")
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--query", required=True)
    parser.add_argument("--max-results", type=int, default=8)
    parser.add_argument("--token-budget", type=int, default=2000)  # ignored
    args = parser.parse_args(argv)

    terms = [t for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", args.query)
             if len(t) > 3]
    scored: list[tuple[int, Path, str]] = []
    for path in sorted(args.repo.rglob("*")):
        if not path.is_file() or path.suffix not in {
            ".py", ".md", ".yaml", ".yml", ".json", ".toml", ".txt"
        }:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        matches = sum(text.count(t) for t in terms)
        if matches:
            scored.append((matches, path, text))

    scored.sort(key=lambda item: -item[0])
    chunks = [
        {
            "file": path.relative_to(args.repo).as_posix(),
            "start_line": 1,
            "end_line": max(1, len(text.splitlines())),
            "content": text,
            "score": float(matches),
        }
        for matches, path, text in scored[: args.max_results]
    ]
    json.dump({"chunks": chunks}, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
