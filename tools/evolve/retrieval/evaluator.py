"""LocalEvaluator — the fitness function for the retrieval evolution loop.

Runs a candidate search-tool program against every benchmark task in the
mock monorepo and scores it on the core efficiency metrics from use
case 3:

* **recall** — coverage-weighted: each ground-truth span scores
  covered_lines / span_lines (union over all verified chunks). A 1-line
  pointer at a 20-line function earns 0.05, not 1.0.
* **precision** — fraction of returned tokens that fall inside
  ground-truth spans. The anti-bloat metric.
* **mrr** — reciprocal rank of the first chunk that covers >= 50% of
  some ground-truth span on its own; rewards putting a *usable* answer
  first.
* **economy** — absolute token-cost term: min(1, target/returned).
  Keeps "return everything" strategies from hiding behind high recall.

``combined_score = 0.35*recall + 0.35*precision + 0.15*mrr + 0.15*economy``

Anti-reward-hacking measures (each defeats an exploit demonstrated by
the red-team audit):

* Chunk content is verified against the file on disk; mismatching or
  out-of-tree chunks earn zero relevance but still pay token cost
  (computed from the on-disk slice, never from self-reported content).
* Candidates run as subprocesses with an EMPTY working directory and a
  minimal environment — no cwd/env breadcrumbs pointing at the
  benchmark workdir.
* Crashes, hangs, malformed JSON, and non-UTF-8 output score zero on
  the affected task and never kill the loop.

The benchmark manifest itself is kept off disk by the evaluate.py
harness (see ``_ensure_benchmark``) so there is no answer key for a
candidate to find.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

_WEIGHT_RECALL = 0.35
_WEIGHT_PRECISION = 0.35
_WEIGHT_MRR = 0.15
_WEIGHT_ECONOMY = 0.15

# A tool that answers a task in ~one or two tight spans lands around
# this many tokens; anything above it starts losing the economy term.
_TOKEN_TARGET = 384

# A chunk must cover at least this fraction of a ground-truth span by
# itself to count as "the answer" for MRR purposes.
_MRR_COVERAGE = 0.5

# Directory from which ``import evolve`` succeeds (tools/).
_EVOLVE_IMPORTABLE_ROOT = Path(__file__).resolve().parents[2]


def _safe_subprocess_env() -> dict[str, str]:
    """Build a minimal, leak-free environment for candidate subprocesses.

    Only PATH, SYSTEMROOT, and LANG are forwarded verbatim. PYTHONPATH
    is filtered to remove any entry from which the ``evolve`` package is
    importable (i.e., any directory containing an ``evolve/`` subtree).
    HOME, VIRTUAL_ENV, and PYTHONHOME are deliberately excluded: they
    let a candidate locate the committed manifest and inflate its score.
    """
    _ALLOWED_PATH_DIRS = {"/usr/bin", "/usr/local/bin", "/bin", "/usr/sbin", "/sbin"}

    env: dict[str, str] = {}
    for key in ("SYSTEMROOT", "LANG"):
        val = os.environ.get(key)
        if val is not None:
            env[key] = val

    raw_path = os.environ.get("PATH", "")
    python_bin_dir = str(Path(sys.executable).resolve().parent)
    safe_path_entries: list[str] = []
    for entry in raw_path.split(os.pathsep):
        if not entry:
            continue
        resolved = str(Path(entry).resolve())
        if "/home/" in resolved or "~" in entry:
            continue
        if resolved in _ALLOWED_PATH_DIRS or resolved == python_bin_dir:
            safe_path_entries.append(entry)
    if safe_path_entries:
        env["PATH"] = os.pathsep.join(safe_path_entries)

    raw_pypath = os.environ.get("PYTHONPATH", "")
    if raw_pypath:
        safe_entries: list[str] = []
        for entry in raw_pypath.split(os.pathsep):
            if not entry:
                continue
            try:
                resolved = Path(entry).resolve()
            except (OSError, ValueError):
                continue
            # Drop entries from which ``import evolve`` would succeed.
            if (resolved / "evolve").is_dir():
                continue
            # Drop entries that are ancestors of the evolve package.
            if _EVOLVE_IMPORTABLE_ROOT.is_relative_to(resolved):
                continue
            safe_entries.append(entry)
        if safe_entries:
            env["PYTHONPATH"] = os.pathsep.join(safe_entries)

    return env


def _estimate_tokens(text: str) -> int:
    """Must match the frozen contract in search_tool.estimate_tokens."""
    return max(1, len(text) // 4) if text else 0


def compute_lift(candidate: dict, default: dict) -> dict:
    """Lift of a candidate's metrics over the default behavior's."""
    c_score, d_score = candidate["combined_score"], default["combined_score"]
    c_tok = candidate["avg_tokens_returned"]
    d_tok = default["avg_tokens_returned"]
    return {
        "combined_score_delta": round(c_score - d_score, 4),
        "combined_score_lift_pct": (
            round((c_score - d_score) / d_score * 100, 1) if d_score > 0 else 0.0
        ),
        "token_reduction_pct": (
            round((d_tok - c_tok) / d_tok * 100, 1) if d_tok > 0 else 0.0
        ),
        "recall_delta": round(candidate["recall"] - default["recall"], 4),
        "precision_delta": round(
            candidate["precision"] - default["precision"], 4
        ),
        "mrr_delta": round(candidate["mrr"] - default["mrr"], 4),
    }


class LocalEvaluator:
    """Scores candidate search tools against the benchmark manifest."""

    def __init__(
        self,
        repo_root: Path | str,
        manifest: dict,
        max_results: int = 8,
        token_budget: int = 2000,
        timeout: float = 20.0,
        max_workers: int = 8,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.tasks = manifest["tasks"]
        self.max_results = max_results
        self.token_budget = token_budget
        self.timeout = timeout
        self.max_workers = max_workers
        self._file_cache: dict[str, list[str] | None] = {}

    # ------------------------------------------------------------------
    def evaluate_program(self, program_path: Path | str) -> dict:
        """Run every benchmark task against ``program_path``.

        Returns a metrics dict whose ``combined_score`` (0..1, higher is
        better) is the AlphaEvolve fitness signal.
        """
        program_path = str(Path(program_path).resolve())
        with tempfile.TemporaryDirectory(prefix="evolve-sandbox-") as sandbox:
            with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                per_task = list(
                    pool.map(
                        lambda t: self._run_task(program_path, t, sandbox),
                        self.tasks,
                    )
                )

        n = len(per_task) or 1
        failures = sum(1 for r in per_task if r["failed"])
        recall = sum(r["recall"] for r in per_task) / n
        precision = sum(r["precision"] for r in per_task) / n
        mrr = sum(r["rr"] for r in per_task) / n
        economy = sum(r["economy"] for r in per_task) / n
        avg_tokens = sum(r["tokens"] for r in per_task) / n
        combined = (
            _WEIGHT_RECALL * recall
            + _WEIGHT_PRECISION * precision
            + _WEIGHT_MRR * mrr
            + _WEIGHT_ECONOMY * economy
        )
        by_kind: dict[str, dict] = {}
        for task, result in zip(self.tasks, per_task):
            stats = by_kind.setdefault(
                task["kind"], {"recall": 0.0, "num_tasks": 0}
            )
            stats["recall"] += result["recall"]
            stats["num_tasks"] += 1
        for stats in by_kind.values():
            stats["recall"] = round(stats["recall"] / stats["num_tasks"], 4)
        return {
            "by_kind": by_kind,
            "combined_score": round(combined, 4),
            "recall": round(recall, 4),
            "precision": round(precision, 4),
            "mrr": round(mrr, 4),
            "economy": round(economy, 4),
            "avg_tokens_returned": round(avg_tokens, 1),
            "failures": failures,
            "num_tasks": len(per_task),
        }

    # ------------------------------------------------------------------
    def _run_task(self, program_path: str, task: dict, sandbox: str) -> dict:
        failed = {
            "failed": True, "recall": 0.0, "precision": 0.0,
            "rr": 0.0, "economy": 0.0, "tokens": 0,
        }
        try:
            cmd = [
                sys.executable,
                program_path,
                "--repo", str(self.repo_root),
                "--query", task["query"],
                "--max-results", str(self.max_results),
                "--token-budget", str(self.token_budget),
            ]
            if sys.platform == "linux":
                unshare = shutil.which("unshare")
                if unshare is None:
                    raise RuntimeError(
                        "PID namespace isolation required for fitness "
                        "evaluation; install util-linux"
                    )
                cmd = [
                    unshare, "--user", "--pid", "--fork", "--mount-proc",
                ] + cmd
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                cwd=sandbox,
                env=_safe_subprocess_env(),
            )
        except (subprocess.TimeoutExpired, OSError):
            return failed
        if proc.returncode != 0:
            return failed
        try:
            chunks = json.loads(proc.stdout)["chunks"]
            if not isinstance(chunks, list):
                return failed
            return self._score_chunks(chunks, task["expected_spans"])
        except Exception:
            return failed

    # ------------------------------------------------------------------
    def _read_lines(self, rel_file: str) -> list[str] | None:
        """Cached read of a repo file; None if outside the tree/missing."""
        if rel_file in self._file_cache:
            return self._file_cache[rel_file]
        lines: list[str] | None = None
        try:
            path = (self.repo_root / rel_file).resolve()
            if path.is_relative_to(self.repo_root) and path.is_file():
                lines = path.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
        except OSError:
            lines = None
        self._file_cache[rel_file] = lines
        return lines

    def _verify_chunk(self, chunk: dict) -> dict | None:
        """Validate a chunk against the repo on disk.

        Returns a normalized chunk with ``tokens`` computed from the
        actual file slice and ``verified`` reflecting whether the
        claimed content matches reality. Fabricated content earns no
        relevance but still pays for the tokens the span would cost.
        Returns None for chunks that reference nothing real.
        """
        try:
            rel = str(chunk["file"]).replace("\\", "/")
            start = int(chunk["start_line"])
            end = int(chunk["end_line"])
            content = str(chunk.get("content", ""))
        except (KeyError, TypeError, ValueError):
            return None
        if start < 1 or end < start:
            return None
        lines = self._read_lines(rel)
        if lines is None:
            return None
        end = min(end, len(lines))
        if start > len(lines):
            return None
        actual = lines[start - 1 : end]
        claimed = content.splitlines()
        verified = len(claimed) == len(actual) and all(
            a.rstrip() == c.rstrip() for a, c in zip(actual, claimed)
        )
        return {
            "file": rel,
            "start_line": start,
            "end_line": end,
            "tokens": _estimate_tokens("\n".join(actual)),
            "verified": verified,
        }

    def _score_chunks(self, raw_chunks: list, spans: list[dict]) -> dict:
        chunks = []
        for item in raw_chunks:
            if isinstance(item, dict):
                normalized = self._verify_chunk(item)
                if normalized:
                    chunks.append(normalized)

        def overlap(chunk: dict, span: dict) -> int:
            if chunk["file"] != span["file"]:
                return 0
            lo = max(chunk["start_line"], span["start_line"])
            hi = min(chunk["end_line"], span["end_line"])
            return max(0, hi - lo + 1)

        verified = [c for c in chunks if c["verified"]]

        # Coverage-weighted recall: union of verified-chunk lines per span.
        recall = 0.0
        for span in spans:
            span_lines = span["end_line"] - span["start_line"] + 1
            covered: set[int] = set()
            for chunk in verified:
                if chunk["file"] != span["file"]:
                    continue
                lo = max(chunk["start_line"], span["start_line"])
                hi = min(chunk["end_line"], span["end_line"])
                covered.update(range(lo, hi + 1))
            recall += len(covered) / span_lines
        recall /= len(spans)

        # MRR: first chunk that alone covers >= _MRR_COVERAGE of a span.
        rr = 0.0
        for rank, chunk in enumerate(chunks, start=1):
            if not chunk["verified"]:
                continue
            if any(
                overlap(chunk, s) / (s["end_line"] - s["start_line"] + 1)
                >= _MRR_COVERAGE
                for s in spans
            ):
                rr = 1.0 / rank
                break

        total_tokens = 0
        relevant_tokens = 0.0
        for chunk in chunks:
            total_tokens += chunk["tokens"]
            if not chunk["verified"]:
                continue  # fabricated content: cost, no credit
            n_lines = chunk["end_line"] - chunk["start_line"] + 1
            hit_lines: set[int] = set()
            for span in spans:
                if chunk["file"] != span["file"]:
                    continue
                lo = max(chunk["start_line"], span["start_line"])
                hi = min(chunk["end_line"], span["end_line"])
                if lo <= hi:
                    hit_lines.update(range(lo, hi + 1))
            relevant_tokens += chunk["tokens"] * (len(hit_lines) / n_lines)
        precision = relevant_tokens / total_tokens if total_tokens else 0.0
        economy = (
            min(1.0, _TOKEN_TARGET / total_tokens) if total_tokens else 0.0
        )

        return {
            "failed": False,
            "recall": recall,
            "precision": precision,
            "rr": rr,
            "economy": economy,
            "tokens": total_tokens,
        }
