"""LocalEvaluator — the fitness function for the retrieval evolution loop.

Runs a candidate search-tool program against every benchmark task in the
mock monorepo and scores it on the core efficiency metrics from use
case 3:

* **recall** — did the tool surface the ground-truth spans at all?
* **precision** — what fraction of the returned tokens were actually
  relevant? This is the anti-bloat metric: a grep-style tool that
  returns a 1,000-line file to answer a 15-line question is crushed
  here.
* **mrr** — mean reciprocal rank of the first relevant chunk; rewards
  putting the right answer first so the agent doesn't need extra turns.
* **token cost** — average tokens returned per call (reported, and
  folded into precision).

Candidates run as sandboxed subprocesses with a hard timeout: an
LLM-mutated program that crashes, hangs, or emits garbage simply scores
zero on the affected tasks instead of killing the loop.
"""

from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

_WEIGHT_RECALL = 0.5
_WEIGHT_PRECISION = 0.3
_WEIGHT_MRR = 0.2


def _estimate_tokens(text: str) -> int:
    """Must match the frozen contract in search_tool.estimate_tokens."""
    return max(1, len(text) // 4) if text else 0


def _overlap_lines(chunk: dict, span: dict) -> int:
    if chunk["file"] != span["file"]:
        return 0
    lo = max(chunk["start_line"], span["start_line"])
    hi = min(chunk["end_line"], span["end_line"])
    return max(0, hi - lo + 1)


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
        self.repo_root = Path(repo_root)
        self.tasks = manifest["tasks"]
        self.max_results = max_results
        self.token_budget = token_budget
        self.timeout = timeout
        self.max_workers = max_workers

    # ------------------------------------------------------------------
    def evaluate_program(self, program_path: Path | str) -> dict:
        """Run every benchmark task against ``program_path``.

        Returns a metrics dict whose ``combined_score`` (0..1, higher is
        better) is the AlphaEvolve fitness signal.
        """
        program_path = str(program_path)
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            per_task = list(
                pool.map(lambda t: self._run_task(program_path, t), self.tasks)
            )

        n = len(per_task) or 1
        failures = sum(1 for r in per_task if r["failed"])
        recall = sum(r["recall"] for r in per_task) / n
        precision = sum(r["precision"] for r in per_task) / n
        mrr = sum(r["rr"] for r in per_task) / n
        avg_tokens = sum(r["tokens"] for r in per_task) / n
        combined = (
            _WEIGHT_RECALL * recall
            + _WEIGHT_PRECISION * precision
            + _WEIGHT_MRR * mrr
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
            "avg_tokens_returned": round(avg_tokens, 1),
            "failures": failures,
            "num_tasks": len(per_task),
        }

    # ------------------------------------------------------------------
    def _run_task(self, program_path: str, task: dict) -> dict:
        failed = {"failed": True, "recall": 0.0, "precision": 0.0,
                  "rr": 0.0, "tokens": 0}
        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    program_path,
                    "--repo", str(self.repo_root),
                    "--query", task["query"],
                    "--max-results", str(self.max_results),
                    "--token-budget", str(self.token_budget),
                ],
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except (subprocess.TimeoutExpired, OSError):
            return failed
        if proc.returncode != 0:
            return failed
        try:
            chunks = json.loads(proc.stdout)["chunks"]
            assert isinstance(chunks, list)
        except (json.JSONDecodeError, KeyError, TypeError, AssertionError):
            return failed
        return self._score_chunks(chunks, task["expected_spans"])

    def _score_chunks(self, chunks: list, spans: list[dict]) -> dict:
        chunks = [c for c in chunks if isinstance(c, dict)]
        covered = sum(
            1 for s in spans if any(_overlap_lines(c, s) for c in chunks)
        )
        recall = covered / len(spans)

        rr = 0.0
        for rank, chunk in enumerate(chunks, start=1):
            if any(_overlap_lines(chunk, s) for s in spans):
                rr = 1.0 / rank
                break

        total_tokens = 0
        relevant_tokens = 0.0
        for chunk in chunks:
            tokens = _estimate_tokens(str(chunk.get("content", "")))
            total_tokens += tokens
            n_lines = max(1, chunk["end_line"] - chunk["start_line"] + 1)
            hit_lines = set()
            for span in spans:
                lo = max(chunk["start_line"], span["start_line"])
                hi = min(chunk["end_line"], span["end_line"])
                if chunk["file"] == span["file"] and lo <= hi:
                    hit_lines.update(range(lo, hi + 1))
            relevant_tokens += tokens * (len(hit_lines) / n_lines)
        precision = relevant_tokens / total_tokens if total_tokens else 0.0

        return {
            "failed": False,
            "recall": recall,
            "precision": precision,
            "rr": rr,
            "tokens": total_tokens,
        }
