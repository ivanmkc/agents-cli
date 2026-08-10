"""Build a replayable eval set from mined retrieval episodes.

``log_mining`` turns benchmark transcripts into *episodes* — real,
measured retrieval workloads (steps, tokens, wall seconds). This module
turns the replayable subset of those episodes into benchmark *cases* in
the same manifest shape ``LocalEvaluator`` already scores
(``query`` / ``expected_spans`` / ``kind``), with the real agent's
observed cost attached for lift comparison.

Two families cover the dominant workloads in the transcripts:

* **sdk-symbol** — reverse-engineering the installed SDK
  (``grep "class LlmAgent" .../site-packages/google/adk/...``).
  Replayed against a checkout of the SDK; ground truth is the
  AST-located definition span of each symbol the agent hunted.
* **project-file** — orienting inside the scaffolded project
  (reads of ``app/agent.py``, ``pyproject.toml``). Replayed against
  the scaffold; ground truth is the whole file.

Episodes that cannot be replayed — environment probes (``pip show``,
``which``), symbols absent from the corpus, files absent from the
scaffold — produce no case.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

try:
    from evolve.retrieval.schema import (
        FAMILY_DEPENDENCY_SYMBOL,
        FAMILY_TOOL_INTERNALS,
        FAMILY_WORKSPACE_FILE,
    )
except ImportError:  # invoked standalone (python -m from tools/)
    from .schema import (
        FAMILY_DEPENDENCY_SYMBOL,
        FAMILY_TOOL_INTERNALS,
        FAMILY_WORKSPACE_FILE,
    )

# Corpus names this ADK-specific miner emits; the schema itself is
# framework-agnostic — other miners declare their own corpora.
SDK_CORPUS_NAME = "adk-sdk"
PROJECT_CORPUS_NAME = "agent-project"
TOOL_SRC_CORPUS_NAME = "agents-cli-src"

# Substrings of a call argument that mark it as an SDK lookup.
_SDK_PATH_MARKERS = ("site-packages/google/adk", "site-packages\\google\\adk")
# The CLI tool's own installed source — debugging the tool, not the SDK.
_CLI_SRC_MARKERS = ("google/agents/cli", "google\\agents\\cli")

# Words that look like identifiers but never name an SDK symbol.
_STOPWORDS = {
    "class", "def", "import", "from", "print", "return", "self", "async",
    "grep", "find", "cat", "head", "tail", "sed", "awk", "python", "python3",
    "pip", "pip3", "adk", "google", "venv", "lib", "site", "packages",
    "the", "a", "an", "is", "how", "what", "where", "type", "name", "https",
    "TypeAlias", "Optional", "None", "True", "False", "str", "int", "dict",
    "list",
}

# A symbol worth hunting is CamelCase or snake_case — a bare lowercase
# word ("callback", "template") is a topic, not a definition to locate.
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _is_symbolish(word: str) -> bool:
    if word in _STOPWORDS or len(word) < 3:
        return False
    if word.startswith("__") and word.endswith("__"):
        return False
    has_lower = re.search(r"[a-z]", word)
    has_upper = re.search(r"[A-Z]", word)
    if has_lower and has_upper:
        return True  # CamelCase / mixedCase
    return "_" in word.strip("_")


def extract_symbols(episode: dict) -> list[str]:
    """Candidate SDK symbols an episode was hunting, best-first.

    Symbols come from the grep/read arguments and the assistant message
    that opened the episode. Path components are split first so
    ``.../llm_agent.py`` doesn't leak ``py`` or glue words together.
    """
    seen: dict[str, None] = {}
    texts = [arg for _, arg in episode.get("calls", ())]
    texts.append(episode.get("context") or "")
    for text in texts:
        # Drop path-y tokens: they name files, not symbols to define.
        for token in re.split(r"\s+", text):
            if "/" in token or "\\" in token:
                continue
            for word in _IDENT.findall(token):
                if _is_symbolish(word) and word not in seen:
                    seen[word] = None
    return list(seen)


def locate_definition(root: Path | str, symbol: str) -> dict | None:
    """AST-located span of ``symbol``'s class/function definition.

    Scans every ``*.py`` under ``root``; returns
    ``{"file", "start_line", "end_line"}`` (file relative to root,
    forward slashes) for the first definition found, or None.
    """
    root = Path(root)
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(
                node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                found = node.name == symbol
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                # Module/class-level assignment: TypeAlias, registry dicts.
                targets = (
                    node.targets if isinstance(node, ast.Assign)
                    else [node.target]
                )
                found = any(
                    isinstance(t, ast.Name) and t.id == symbol
                    for t in targets
                )
            else:
                continue
            if found:
                return {
                    "file": path.relative_to(root).as_posix(),
                    "start_line": node.lineno,
                    "end_line": node.end_lineno or node.lineno,
                }
    return None


# ----------------------------------------------------------------------
# Project-file family helpers

# Leading directories transcripts prepend to project paths.
_PROJECT_PREFIXES = re.compile(
    r"^(?:/tmp/agent-workspace/)?(?:\./)?(?:agent-project/)?"
)


def _project_rel_paths(arg: str) -> list[str]:
    """Project-relative candidate paths mentioned in one call argument."""
    out = []
    for token in re.findall(r"[\w./\-{}]+", arg):
        if "site-packages" in token or token.startswith(("http", "-")):
            continue
        rel = _PROJECT_PREFIXES.sub("", token)
        if rel and not rel.startswith(("/", ".")) and ("/" in rel or "." in rel):
            out.append(rel)
    return out


def _file_span(root: Path, rel: str) -> dict | None:
    path = root / rel
    if not path.is_file():
        return None
    try:
        n = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        return None
    return {"file": rel, "start_line": 1, "end_line": max(1, n)}


# ----------------------------------------------------------------------

def _observed(episode: dict) -> dict:
    return {
        "steps": episode.get("steps"),
        "tokens": episode.get("tokens"),
        "wall_seconds": episode.get("wall_seconds"),
        "generator": episode.get("generator"),
        "case": episode.get("case"),
        "run": episode.get("run"),
        "stream": episode.get("stream"),
    }


def _query(episode: dict, symbols: list[str]) -> str:
    context = (episode.get("context") or "").strip()
    missing = [s for s in symbols if s not in context]
    if context and not missing:
        return context
    if context:
        return f"{context} ({' '.join(missing)})"
    return "Find the definition of " + " and ".join(symbols)


def build_cases(
    episodes: list[dict],
    sdk_root: Path | str | None,
    project_root: Path | str | None,
    cli_src_root: Path | str | None = None,
) -> list[dict]:
    """Replayable benchmark cases from mined episodes.

    Routing, checked in order per episode:

    * touched the CLI tool's own installed source (``google/agents/cli``)
      -> ``tool-internals`` (symbols AST-located in ``cli_src_root``);
    * touched ``site-packages`` (the installed SDK)
      -> ``dependency-symbol`` (symbols located in ``sdk_root``);
    * otherwise its project-file reads -> one ``workspace-file`` case.

    Unreplayable episodes (symbols/files not locatable in the given
    corpus, or no root supplied for their corpus) are dropped. Each case
    carries the episode's miner-derived ``motivation`` when present.
    """
    sdk_root = Path(sdk_root) if sdk_root else None
    project_root = Path(project_root) if project_root else None
    cli_src_root = Path(cli_src_root) if cli_src_root else None
    sdk_span_cache: dict[str, dict | None] = {}
    cli_span_cache: dict[str, dict | None] = {}
    cases: list[dict] = []

    def _with_motivation(case: dict, episode: dict) -> dict:
        motivation = episode.get("motivation")
        if motivation:
            case["motivation"] = motivation
        return case

    for episode in episodes:
        calls = episode.get("calls") or []
        is_cli_src = any(
            any(m in arg for m in _CLI_SRC_MARKERS) for _, arg in calls
        )
        is_sdk = not is_cli_src and any(
            any(m in arg for m in _SDK_PATH_MARKERS)
            or "site-packages" in arg
            for _, arg in calls
        )
        if is_cli_src:
            if cli_src_root is None:
                continue  # no corpus for tool internals -> unreplayable
            spans = []
            located_symbols = []
            for symbol in extract_symbols(episode):
                if symbol not in cli_span_cache:
                    cli_span_cache[symbol] = locate_definition(
                        cli_src_root, symbol
                    )
                span = cli_span_cache[symbol]
                if span:
                    spans.append(span)
                    located_symbols.append(symbol)
            if spans:
                cases.append(_with_motivation({
                    "family": FAMILY_TOOL_INTERNALS,
                    "corpus": TOOL_SRC_CORPUS_NAME,
                    "query": _query(episode, located_symbols),
                    "symbols": located_symbols,
                    "expected_spans": spans,
                    "observed": _observed(episode),
                }, episode))
            continue
        if is_sdk and sdk_root is not None:
            spans = []
            located_symbols = []
            for symbol in extract_symbols(episode):
                if symbol not in sdk_span_cache:
                    sdk_span_cache[symbol] = locate_definition(sdk_root, symbol)
                span = sdk_span_cache[symbol]
                if span:
                    spans.append(span)
                    located_symbols.append(symbol)
            if spans:
                cases.append(_with_motivation({
                    "family": FAMILY_DEPENDENCY_SYMBOL,
                    "corpus": SDK_CORPUS_NAME,
                    "query": _query(episode, located_symbols),
                    "symbols": located_symbols,
                    "expected_spans": spans,
                    "observed": _observed(episode),
                }, episode))
            continue

        if project_root is not None:
            spans, seen = [], set()
            for _, arg in calls:
                for rel in _project_rel_paths(arg):
                    if rel in seen:
                        continue
                    seen.add(rel)
                    span = _file_span(project_root, rel)
                    if span:
                        spans.append(span)
            if spans:
                cases.append(_with_motivation({
                    "family": FAMILY_WORKSPACE_FILE,
                    "corpus": PROJECT_CORPUS_NAME,
                    "query": _query(episode, []),
                    "expected_spans": spans,
                    "observed": _observed(episode),
                }, episode))
    return cases


# ----------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("episodes", type=Path,
                        help="JSONL of mined episodes (from log_mining)")
    parser.add_argument("--sdk-root", type=Path, default=None)
    parser.add_argument("--project-root", type=Path, default=None)
    parser.add_argument("--cli-src-root", type=Path, default=None)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    episodes = [
        json.loads(line)
        for line in args.episodes.read_text().splitlines()
        if line.strip()
    ]
    cases = build_cases(episodes, args.sdk_root, args.project_root,
                        cli_src_root=args.cli_src_root)
    manifest = {
        "source_episodes": len(episodes),
        "tasks": cases,
    }
    args.out.write_text(json.dumps(manifest, indent=2) + "\n")
    by_family: dict[str, int] = {}
    for case in cases:
        by_family[case["family"]] = by_family.get(case["family"], 0) + 1
    print(f"{len(cases)} cases from {len(episodes)} episodes -> {args.out}")
    for family, count in sorted(by_family.items()):
        print(f"  {family}: {count}")


if __name__ == "__main__":
    main()
