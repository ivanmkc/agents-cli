"""Deterministic mock-monorepo generator for the retrieval benchmark.

Generates a synthetic multi-package Python monorepo plus a benchmark
manifest of retrieval tasks ("Where is class X defined?", "Find the
modules that use Y", "Where is config key Z set?") with line-accurate
ground-truth spans.

The manifest is written OUTSIDE the generated tree (``<repo>.tasks.json``
next to the repo directory) so an evolved search tool can never cheat by
reading the answer key.

Everything is seeded — same (seed, scale) always produces byte-identical
trees, which keeps AlphaEvolve fitness scores comparable across
generations.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

_PACKAGES = ["auth", "billing", "search", "notify", "pipeline", "storage"]

_CLASS_SUFFIXES = [
    "Middleware",
    "Manager",
    "Client",
    "Registry",
    "Resolver",
    "Gateway",
    "Scheduler",
    "Ledger",
]

_FUNC_VERBS = [
    "rotate_keys",
    "flush_cache",
    "resolve_route",
    "emit_metrics",
    "load_snapshot",
    "prune_stale",
    "sync_replicas",
    "audit_access",
]

_CONFIG_PARAMS = [
    "retry_limit",
    "timeout_seconds",
    "max_connections",
    "cache_ttl",
]

# Vendored noise is salted with these common words so that naive
# keyword search produces false positives.
_NOISE_WORDS = ["process", "config", "handler", "request", "token", "cache"]


class _FileBuilder:
    """Accumulates lines and reports 1-indexed line spans."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def add(self, *lines: str) -> None:
        self.lines.extend(lines)

    def next_line(self) -> int:
        return len(self.lines) + 1

    def current_line(self) -> int:
        return len(self.lines)

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(self.lines) + "\n")


def _camel(name: str) -> str:
    return name.capitalize()


def _emit_class(builder: _FileBuilder, name: str, pkg: str) -> tuple[int, int]:
    start = builder.next_line()
    builder.add(
        f"class {name}:",
        f'    """Core {pkg} component: {name}."""',
        "",
        "    def __init__(self, config):",
        "        self.config = config",
        "        self._cache = {}",
        "",
        "    def process(self, request):",
        f'        """Handle a {pkg} request."""',
        '        key = request.get("token")',
        "        if key in self._cache:",
        "            return self._cache[key]",
        "        result = self._validate(key)",
        "        self._cache[key] = result",
        "        return result",
        "",
        "    def _validate(self, key):",
        "        return bool(key) and len(str(key)) > 8",
    )
    return start, builder.current_line()


def _emit_function(builder: _FileBuilder, name: str, pkg: str) -> tuple[int, int]:
    start = builder.next_line()
    builder.add(
        f"def {name}(config, items):",
        f'    """Utility for the {pkg} package: {name}."""',
        "    results = []",
        "    for item in items:",
        f'        if item.get("package") == "{pkg}":',
        "            results.append(item)",
        '    limit = config.get("limit", 10)',
        "    return results[:limit]",
    )
    return start, builder.current_line()


def _emit_filler(builder: _FileBuilder, pkg: str, serial: int,
                 rng: random.Random) -> None:
    """Realistic distractor code that is never a ground-truth answer.

    Bulks module files to a few hundred lines so that a tool returning
    whole files (grep-style) pays a visible precision/token penalty.
    """
    builder.add(
        f"_DEFAULTS_{serial} = {{",
        f'    "region": "region-{rng.randint(1, 9)}",',
        f'    "attempts": {rng.randint(1, 9)},',
        f'    "backoff_ms": {rng.randint(10, 900)},',
        "}",
        "",
        "",
    )
    for i in range(3):
        word = rng.choice(_NOISE_WORDS)
        builder.add(
            f"def _{pkg}_helper_{serial}_{i}(payload, retries=2):",
            f'    """Internal {pkg} helper; wraps {word} bookkeeping."""',
            f"    state = dict(_DEFAULTS_{serial})",
            "    for attempt in range(retries):",
            f'        state["{word}"] = payload.get("{word}")',
            f'        if state.get("{word}") is not None:',
            "            break",
            '        state["attempts"] = attempt + 1',
            "    return state",
            "",
            "",
        )


def _emit_noise_file(path: Path, rng: random.Random, n_defs: int) -> None:
    builder = _FileBuilder()
    builder.add('"""Auto-generated vendored code. Do not edit."""', "")
    for i in range(n_defs):
        word_a = rng.choice(_NOISE_WORDS)
        word_b = rng.choice(_NOISE_WORDS)
        builder.add(
            f"def generated_{word_a}_{i}({word_b}, options=None):",
            f'    """Generated shim wrapping {word_a} for {word_b}."""',
            "    options = options or {}",
            f"    payload = dict(options, kind='{word_a}')",
            f"    payload['{word_b}'] = {word_b}",
            "    return payload",
            "",
        )
    builder.write(path)


def generate_monorepo(
    repo_root: Path | str,
    seed: int = 0,
    scale: int = 3,
    write_tasks: bool = True,
) -> dict:
    """Generate the mock monorepo and its benchmark manifest.

    Args:
        repo_root: Directory to create the repo in.
        seed: RNG seed; same seed + scale is fully reproducible.
        scale: Size multiplier. The default yields >= 100 tasks.

    Returns:
        The manifest dict (also written to ``<repo_root>.tasks.json``).
    """
    repo_root = Path(repo_root)
    rng = random.Random(seed)
    tasks: list[dict] = []
    # symbol -> {"definition": span-dict, "usages": [span-dict, ...]}
    symbols: dict[str, dict] = {}
    importable: list[tuple[str, str]] = []  # (module_path, symbol)

    # --- packages/: class + function definitions -------------------------
    for pkg in _PACKAGES:
        for mod_idx in range(2 * scale):
            builder = _FileBuilder()
            builder.add(f'"""{pkg} package, module {mod_idx}."""', "")
            rel = Path("packages") / pkg / f"module_{mod_idx}.py"
            for sym_idx in range(2):
                serial = mod_idx * 2 + sym_idx
                if sym_idx % 2 == 0:
                    suffix = _CLASS_SUFFIXES[serial % len(_CLASS_SUFFIXES)]
                    name = f"{_camel(pkg)}{suffix}{serial}"
                    start, end = _emit_class(builder, name, pkg)
                else:
                    verb = _FUNC_VERBS[serial % len(_FUNC_VERBS)]
                    name = f"{pkg}_{verb}_{serial}"
                    start, end = _emit_function(builder, name, pkg)
                builder.add("", "")
                span = {"file": rel.as_posix(), "start_line": start, "end_line": end}
                symbols[name] = {"definition": span, "usages": []}
                module_path = f"packages.{pkg}.module_{mod_idx}"
                importable.append((module_path, name))
                # Distractor bulk between real symbols: whole-file
                # retrieval must cost real tokens, as in a live monorepo.
                for filler_idx in range(3):
                    _emit_filler(builder, pkg, serial * 10 + filler_idx, rng)
            builder.write(repo_root / rel)

    # --- services/: usage sites ------------------------------------------
    used_symbols = rng.sample(importable, min(8 * scale, len(importable)))
    n_services = 4 * scale
    for svc_idx in range(n_services):
        builder = _FileBuilder()
        rel = Path("services") / f"svc_{svc_idx}" / "main.py"
        builder.add(f'"""Service {svc_idx} entrypoint."""', "")
        svc_uses = [
            (module_path, sym)
            for module_path, sym in used_symbols
            if rng.random() < 0.35
        ]
        for module_path, sym in svc_uses:
            builder.add(f"from {module_path} import {sym}")
        builder.add("", "", "def handle(request, config):")
        if not svc_uses:
            builder.add("    return request")
        for module_path, sym in svc_uses:
            start = builder.next_line()
            if sym[0].isupper():
                builder.add(
                    f"    component = {sym}(config)",
                    "    request = component.process(request)",
                )
            else:
                builder.add(f'    request["items"] = {sym}(config, [request])')
            symbols[sym]["usages"].append(
                {
                    "file": rel.as_posix(),
                    "start_line": start,
                    "end_line": builder.current_line(),
                }
            )
        if svc_uses:
            builder.add("    return request")
        builder.write(repo_root / rel)

    # --- configs/: yaml keys ----------------------------------------------
    config_spans: dict[str, dict] = {}
    for pkg in _PACKAGES:
        builder = _FileBuilder()
        rel = Path("configs") / f"{pkg}.yaml"
        builder.add(f"# Runtime configuration for the {pkg} package", f"{pkg}:")
        for param in _CONFIG_PARAMS:
            key = f"{pkg}_{param}"
            line = builder.next_line()
            builder.add(f"  {key}: {rng.randint(1, 300)}")
            config_spans[key] = {
                "file": rel.as_posix(),
                "start_line": line,
                "end_line": line,
            }
        builder.write(repo_root / rel)

    # --- vendor/: noise ----------------------------------------------------
    for lib_idx in range(2):
        _emit_noise_file(
            repo_root / "vendor" / f"lib_{lib_idx}" / "generated.py",
            rng,
            n_defs=90,
        )

    # --- tasks ---------------------------------------------------------------
    for name, info in sorted(symbols.items()):
        kind = "class" if name[0].isupper() else "function"
        tasks.append(
            {
                "id": f"definition:{name}",
                "kind": "definition",
                "symbol": name,
                "query": f"Where is the {kind} {name} defined?",
                "expected_spans": [info["definition"]],
            }
        )
        if info["usages"]:
            tasks.append(
                {
                    "id": f"usage:{name}",
                    "kind": "usage",
                    "symbol": name,
                    "query": f"Find all downstream code that uses {name}",
                    "expected_spans": info["usages"],
                }
            )
    config_keys = sorted(config_spans)
    for key in [k for i, k in enumerate(config_keys) if i % 2 == 0]:
        tasks.append(
            {
                "id": f"config:{key}",
                "kind": "config",
                "symbol": key,
                "query": f"Where is the config key {key} set?",
                "expected_spans": [config_spans[key]],
            }
        )

    manifest = {"seed": seed, "scale": scale, "tasks": tasks}
    if write_tasks:
        # Answer key for HUMAN inspection only. Evaluation harnesses must
        # pass write_tasks=False so no key exists anywhere a candidate
        # subprocess could find it (see the red-team audit).
        tasks_path = repo_root.parent / f"{repo_root.name}.tasks.json"
        tasks_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo_root", type=Path)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--scale", type=int, default=3)
    args = parser.parse_args()
    result = generate_monorepo(args.repo_root, seed=args.seed, scale=args.scale)
    print(f"Generated {len(result['tasks'])} tasks under {args.repo_root}")
