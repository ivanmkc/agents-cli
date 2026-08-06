"""Evolvable code-search tool — the retrieval layer AlphaEvolve optimizes.

This is the "code-lookup plugin" from use case 3 of the AlphaEvolve
integration strategy: instead of delegating to grep and dragging whole
files into the context window, agents call this tool and get back small,
ranked, line-addressed chunks.

Layout contract (do not change):

* Everything between ``# EVOLVE-BLOCK-START`` and ``# EVOLVE-BLOCK-END``
  is the search algorithm AlphaEvolve mutates.
* Everything outside the block — the ``search()`` signature, the chunk
  schema, the CLI, and the token-budget enforcement — is the frozen
  interface the evaluator (and every agent) relies on. Mutations cannot
  break the contract because the wrapper re-validates whatever the
  evolved code returns.

Chunk schema::

    {"file": str (repo-relative, posix),
     "start_line": int (1-indexed, inclusive),
     "end_line": int (inclusive),
     "content": str,
     "score": float}

CLI::

    python search_tool.py --repo PATH --query "..." \
        [--max-results N] [--token-budget N]

prints ``{"chunks": [...]}`` to stdout.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DEFAULT_MAX_RESULTS = 8
DEFAULT_TOKEN_BUDGET = 2000

_MAX_FILE_BYTES = 1_000_000
_TEXT_SUFFIXES = {
    ".py", ".md", ".txt", ".yaml", ".yml", ".json", ".toml", ".cfg", ".ini",
    ".sh", ".js", ".ts", ".tsx", ".java", ".go", ".rs", ".html", ".css",
}


def estimate_tokens(text: str) -> int:
    """Cheap, deterministic token estimate (~4 chars per token)."""
    return max(1, len(text) // 4) if text else 0


# EVOLVE-BLOCK-START
# Baseline retrieval algorithm. AlphaEvolve mutates ONLY this region.
# Known headroom for evolution: no AST parsing, no import-graph
# awareness for usage queries, no camelCase/snake_case sub-token
# matching, no learned stopwords, single-pass ranking.

_STOPWORDS = {
    "a", "all", "an", "and", "are", "code", "does", "downstream", "find",
    "for", "how", "in", "is", "of", "or", "set", "that", "the", "to",
    "what", "where", "which",
}

_DEF_RE = re.compile(r"^\s*(?:class|def)\s+(\w+)")


def _query_terms(query: str) -> list[str]:
    terms = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", query)
    return [t for t in terms if t.lower() not in _STOPWORDS]


def _iter_source_files(repo_root: Path):
    for path in sorted(repo_root.rglob("*")):
        if not path.is_file() or path.suffix not in _TEXT_SUFFIXES:
            continue
        if any(part.startswith(".") for part in path.relative_to(repo_root).parts):
            continue
        if path.stat().st_size > _MAX_FILE_BYTES:
            continue
        yield path


def _expand_to_block(lines: list[str], hit_idx: int) -> tuple[int, int]:
    """Expand a hit line to its enclosing def/class block, else +-4 lines."""
    start = hit_idx
    for i in range(hit_idx, max(-1, hit_idx - 60), -1):
        stripped = lines[i].lstrip()
        if stripped.startswith(("def ", "class ")):
            start = i
            indent = len(lines[i]) - len(stripped)
            end = hit_idx
            for j in range(hit_idx + 1, min(len(lines), i + 80)):
                body = lines[j]
                if body.strip() and (len(body) - len(body.lstrip())) <= indent:
                    break
                if body.strip():
                    end = j
            return start, end
    return max(0, start - 4), min(len(lines) - 1, hit_idx + 4)


def _evolved_search(
    repo_root: Path,
    query: str,
    max_results: int,
    token_budget: int,
) -> list[dict]:
    terms = _query_terms(query)
    if not terms:
        return []
    lowered = [t.lower() for t in terms]

    # Pass 1: score every matching line in every file.
    # Rare terms (e.g. a specific symbol name) get higher weight than
    # terms that appear all over the tree.
    file_lines: dict[Path, list[str]] = {}
    term_file_counts = {t: 0 for t in lowered}
    per_file_hits: dict[Path, list[tuple[int, float, set[str]]]] = {}

    for path in _iter_source_files(repo_root):
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        text_lower = text.lower()
        present = [t for t in lowered if t in text_lower]
        if not present:
            continue
        for t in present:
            term_file_counts[t] += 1
        file_lines[path] = text.splitlines()

    for path, lines in file_lines.items():
        hits = []
        for idx, line in enumerate(lines):
            line_lower = line.lower()
            matched = {t for t in lowered if t in line_lower}
            if not matched:
                continue
            weight = sum(1.0 / term_file_counts[t] for t in matched)
            defn = _DEF_RE.match(line)
            if defn and defn.group(1).lower() in matched:
                weight *= 3.0  # definitions outrank mentions
            hits.append((idx, weight, matched))
        if hits:
            per_file_hits[path] = hits

    # Pass 2: expand top hits into block-level chunks and merge overlaps.
    chunks: list[dict] = []
    for path, hits in per_file_hits.items():
        lines = file_lines[path]
        rel = path.relative_to(repo_root).as_posix()
        hits.sort(key=lambda h: -h[1])
        spans: list[list] = []  # [start, end, score, matched_terms]
        for idx, weight, matched in hits[:10]:
            start, end = _expand_to_block(lines, idx)
            for span in spans:
                if start <= span[1] and end >= span[0]:
                    span[0] = min(span[0], start)
                    span[1] = max(span[1], end)
                    span[2] = max(span[2], weight)
                    span[3] |= matched
                    break
            else:
                spans.append([start, end, weight, set(matched)])
        for start, end, weight, matched in spans:
            content = "\n".join(lines[start : end + 1])
            chunks.append(
                {
                    "file": rel,
                    "start_line": start + 1,
                    "end_line": end + 1,
                    "content": content,
                    "score": weight * (1.0 + 0.25 * len(matched)),
                }
            )

    chunks.sort(key=lambda c: -c["score"])
    return chunks[: max_results * 4]
# EVOLVE-BLOCK-END


def _sanitize(raw: object, max_results: int, token_budget: int) -> list[dict]:
    """Frozen contract enforcement: schema, result cap, token budget."""
    if not isinstance(raw, list):
        return []
    valid: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            chunk = {
                "file": str(item["file"]),
                "start_line": int(item["start_line"]),
                "end_line": int(item["end_line"]),
                "content": str(item["content"]),
                "score": float(item["score"]),
            }
        except (KeyError, TypeError, ValueError):
            continue
        if Path(chunk["file"]).is_absolute():
            continue
        if chunk["start_line"] < 1 or chunk["start_line"] > chunk["end_line"]:
            continue
        valid.append(chunk)

    budgeted: list[dict] = []
    spent = 0
    for chunk in valid:
        if len(budgeted) >= max_results:
            break
        cost = estimate_tokens(chunk["content"])
        if spent + cost > token_budget:
            if budgeted:
                continue
            # Nothing returned yet: trim the chunk from the bottom to fit.
            lines = chunk["content"].splitlines()
            while lines and spent + estimate_tokens("\n".join(lines)) > token_budget:
                lines.pop()
            if not lines:
                continue
            chunk = dict(
                chunk,
                content="\n".join(lines),
                end_line=chunk["start_line"] + len(lines) - 1,
            )
            cost = estimate_tokens(chunk["content"])
        budgeted.append(chunk)
        spent += cost
    return budgeted


def search(
    repo_root: Path | str,
    query: str,
    max_results: int = DEFAULT_MAX_RESULTS,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> list[dict]:
    """Search ``repo_root`` for code relevant to ``query``.

    Returns at most ``max_results`` chunks whose combined estimated token
    count never exceeds ``token_budget``, ranked most-relevant first.
    """
    raw = _evolved_search(Path(repo_root), query, max_results, token_budget)
    return _sanitize(raw, max_results, token_budget)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evolvable code-search tool")
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--query", required=True)
    parser.add_argument("--max-results", type=int, default=DEFAULT_MAX_RESULTS)
    parser.add_argument("--token-budget", type=int, default=DEFAULT_TOKEN_BUDGET)
    args = parser.parse_args(argv)

    chunks = search(args.repo, args.query, args.max_results, args.token_budget)
    json.dump({"chunks": chunks}, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
