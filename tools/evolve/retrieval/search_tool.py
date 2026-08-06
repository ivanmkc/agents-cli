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
# AST-aware retrieval: parse .py files with the ast module and emit exact
# block-level chunks (full def/class bodies with precise end lines), so
# definition answers carry no surrounding filler. Usage hits map to their
# smallest enclosing function; config hits emit tight YAML key blocks.
# Non-Python files fall back to a small regex window.

import ast as _ast_mod

_STOPWORDS = {
    "a", "all", "an", "and", "are", "code", "does", "find",
    "for", "how", "in", "is", "it", "its", "of", "or", "that", "the", "to",
    "what", "where", "which", "show", "me", "file", "files", "repo",
}

_USAGE_WORDS = {
    "use", "uses", "used", "using", "usage", "usages", "call", "calls",
    "called", "caller", "callers", "calling", "invoke", "invokes", "invoked",
    "import", "imports", "imported", "importing", "depend", "depends",
    "dependency", "dependencies", "downstream", "consumer", "consumers",
    "consume", "consumes", "reference", "references", "referenced",
    "service", "services",
}
_CONFIG_WORDS = {
    "config", "configs", "configuration", "configured", "configures",
    "yaml", "yml", "toml", "setting", "settings", "set", "sets", "value",
    "values", "default", "defaults", "option", "options", "flag", "flags",
    "key", "keys",
}
_DEF_WORDS = {
    "define", "defined", "defines", "definition", "declaration", "declared",
    "implementation", "implemented", "implements", "signature", "source",
    "body", "class", "function", "method", "docstring", "implementing",
}

_CONFIG_SUFFIXES = {".yaml", ".yml", ".toml", ".ini", ".cfg", ".json"}


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


def _intent(terms: list[str]) -> str:
    low = {t.lower() for t in terms}
    if low & _USAGE_WORDS:
        return "usage"
    if low & _CONFIG_WORDS:
        return "config"
    if low & _DEF_WORDS:
        return "definition"
    return "mixed"


def _symbol_candidates(terms: list[str]) -> list[str]:
    reserved = _USAGE_WORDS | _CONFIG_WORDS | _DEF_WORDS
    cands = [t for t in terms if t.lower() not in reserved]
    if not cands:
        cands = list(terms)

    def rank(t: str):
        identish = (
            "_" in t
            or any(c.isupper() for c in t[1:])
            or (t[:1].isupper() and any(c.islower() for c in t))
        )
        return (identish, len(t))

    cands.sort(key=rank, reverse=True)
    seen: set[str] = set()
    out: list[str] = []
    for t in cands:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _py_blocks(text: str):
    """(start0, end0, name, is_class) for every def/class, plus module-level
    assignment targets (constants)."""
    try:
        tree = _ast_mod.parse(text)
    except SyntaxError:
        return None
    blocks = []
    for node in _ast_mod.walk(tree):
        if isinstance(
            node,
            (_ast_mod.FunctionDef, _ast_mod.AsyncFunctionDef, _ast_mod.ClassDef),
        ):
            start = node.lineno
            if node.decorator_list:
                start = min(start, min(d.lineno for d in node.decorator_list))
            end = node.end_lineno or node.lineno
            blocks.append(
                (start - 1, end - 1, node.name, isinstance(node, _ast_mod.ClassDef))
            )
    assigns = []
    for node in tree.body:
        names = []
        if isinstance(node, _ast_mod.Assign):
            names = [t.id for t in node.targets if isinstance(t, _ast_mod.Name)]
        elif isinstance(node, _ast_mod.AnnAssign) and isinstance(
            node.target, _ast_mod.Name
        ):
            names = [node.target.id]
        for n in names:
            assigns.append((node.lineno - 1, (node.end_lineno or node.lineno) - 1, n))
    return blocks, assigns


def _yaml_chunk(lines: list[str], idx: int) -> tuple[int, int]:
    """Matched config line plus its indented children — no extra context."""
    def indent(s: str) -> int:
        return len(s) - len(s.lstrip())

    base = indent(lines[idx])
    end = idx
    j = idx + 1
    while j < len(lines):
        s = lines[j]
        if not s.strip():
            j += 1
            continue
        if indent(s) > base:
            end = j
            j += 1
            continue
        break
    return idx, end


def _group_lines(idxs: list[int], gap: int = 2) -> list[tuple[int, int]]:
    if not idxs:
        return []
    idxs = sorted(set(idxs))
    groups = [[idxs[0], idxs[0]]]
    for i in idxs[1:]:
        if i - groups[-1][1] <= gap:
            groups[-1][1] = i
        else:
            groups.append([i, i])
    return [(a, b) for a, b in groups]


def _evolved_search(
    repo_root: Path,
    query: str,
    max_results: int,
    token_budget: int,
) -> list[dict]:
    terms = _query_terms(query)
    if not terms:
        return []
    intent = _intent(terms)
    candidates = _symbol_candidates(terms)[:3]

    def collect(symbol: str):
        pat = re.compile(
            r"(?<![A-Za-z0-9_])" + re.escape(symbol) + r"(?![A-Za-z0-9_])"
        )
        defs: list[dict] = []
        uses: list[dict] = []
        confs: list[dict] = []
        others: list[dict] = []

        for path in _iter_source_files(repo_root):
            try:
                text = path.read_text(errors="replace")
            except OSError:
                continue
            if not pat.search(text):
                continue
            rel = path.relative_to(repo_root).as_posix()
            lines = text.splitlines()
            hit_idxs = [i for i, line in enumerate(lines) if pat.search(line)]
            if not hit_idxs:
                continue

            top = rel.split("/", 1)[0]
            vendor_pen = (
                0.1 if top in ("vendor", "third_party", "node_modules") else 1.0
            )

            def emit(bucket, start, end, score, loose=False):
                for c in bucket:
                    if c["file"] == rel and start <= c["_e"] and end >= c["_s"]:
                        c["_s"] = min(c["_s"], start)
                        c["_e"] = max(c["_e"], end)
                        c["score"] = max(c["score"], score)
                        c["_loose"] = c.get("_loose", False) and loose
                        return
                bucket.append(
                    {
                        "file": rel,
                        "_s": start,
                        "_e": end,
                        "score": score,
                        "_loose": loose,
                    }
                )

            if path.suffix == ".py":
                parsed = _py_blocks(text)
                if parsed is None:
                    for a, b in _group_lines(hit_idxs, gap=6):
                        emit(
                            others,
                            max(0, a - 3),
                            min(len(lines) - 1, b + 3),
                            vendor_pen,
                        )
                    continue
                blocks, assigns = parsed
                sym_def_spans = []
                for s, e, name, _is_cls in blocks:
                    if name == symbol:
                        bonus = 1.5 if top == "packages" else 1.0
                        emit(defs, s, e, (10.0 + bonus) * vendor_pen)
                        sym_def_spans.append((s, e))
                for s, e, name in assigns:
                    if name == symbol:
                        emit(defs, s, e, 10.0 * vendor_pen)
                        sym_def_spans.append((s, e))
                func_blocks = [
                    (s, e) for s, e, _n, is_cls in blocks if not is_cls
                ]
                svc_bonus = 2.0 if top == "services" else 0.5
                loose: list[int] = []
                for idx in hit_idxs:
                    if any(s <= idx <= e for s, e in sym_def_spans):
                        continue
                    if any(s <= idx <= e for s, e in func_blocks):
                        end = idx
                        m = re.match(r"\s*([A-Za-z_]\w*)\s*=[^=]", lines[idx])
                        if m and idx + 1 < len(lines):
                            tgt = re.compile(
                                r"(?<![A-Za-z0-9_])"
                                + re.escape(m.group(1))
                                + r"(?![A-Za-z0-9_])"
                            )
                            if tgt.search(lines[idx + 1]):
                                end = idx + 1
                        emit(uses, idx, end, (5.0 + svc_bonus) * vendor_pen)
                    else:
                        loose.append(idx)
                for a, b in _group_lines(loose, gap=1):
                    emit(uses, a, b, (4.0 + svc_bonus) * vendor_pen, loose=True)
            elif path.suffix in _CONFIG_SUFFIXES:
                for idx in hit_idxs:
                    s, e = _yaml_chunk(lines, idx)
                    emit(confs, s, e, (7.0 if top == "configs" else 5.0) * vendor_pen)
            else:
                for a, b in _group_lines(hit_idxs, gap=6):
                    emit(
                        others,
                        max(0, a - 3),
                        min(len(lines) - 1, b + 3),
                        vendor_pen,
                    )
        if any(not c.get("_loose") for c in uses):
            uses = [c for c in uses if not c.get("_loose")]
        return defs, uses, confs, others

    defs: list[dict] = []
    uses: list[dict] = []
    confs: list[dict] = []
    others: list[dict] = []
    for cand in candidates:
        defs, uses, confs, others = collect(cand)
        if defs or uses or confs or others:
            break

    text_cache: dict[str, list[str]] = {}

    def finalize(bucket: list[dict], base: float) -> list[dict]:
        out = []
        for c in bucket:
            s, e = c["_s"], c["_e"]
            if c["file"] not in text_cache:
                try:
                    text_cache[c["file"]] = (
                        (repo_root / c["file"]).read_text(errors="replace").splitlines()
                    )
                except OSError:
                    text_cache[c["file"]] = []
            lines = text_cache[c["file"]]
            if not lines:
                continue
            content = "\n".join(lines[s : e + 1])
            out.append(
                {
                    "file": c["file"],
                    "start_line": s + 1,
                    "end_line": e + 1,
                    "content": content,
                    "score": base + c["score"],
                }
            )
        out.sort(key=lambda c: -c["score"])
        return out

    if intent == "definition":
        order = [(defs, 3000.0), (uses, 2000.0), (confs, 1000.0), (others, 0.0)]
        hard = True
    elif intent == "usage":
        order = [(uses, 3000.0), (defs, 2000.0), (confs, 1000.0), (others, 0.0)]
        hard = True
    elif intent == "config":
        order = [(confs, 3000.0), (defs, 2000.0), (uses, 1000.0), (others, 0.0)]
        hard = True
    else:
        order = [(defs, 3000.0), (uses, 2000.0), (confs, 1000.0), (others, 0.0)]
        hard = False

    chunks: list[dict] = []
    for bucket, base in order:
        ranked = finalize(bucket, base)
        if hard and ranked:
            chunks = ranked
            break
        chunks.extend(ranked)

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
