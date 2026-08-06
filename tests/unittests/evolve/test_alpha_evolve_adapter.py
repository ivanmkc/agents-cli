"""Tests for the official AlphaEvolve client-contract adapter.

Contract per the public AlphaEvolve codelab examples
(github.com/Google-Cloud-AI/alphaevolve-on-googlecloud): the evaluation
function receives a ``program_candidate`` dict with the candidate code at
``["content"]["files"][0]["content"]`` and returns an
``AlphaEvolveProgramEvaluation``-shaped dict:
``{"scores": {"scores": [{"metric": ..., "score": ...}]},
   "insights": {"insights": [{"label": ..., "text": ...}]}}``.
"""

from pathlib import Path

from evolve.retrieval import search_tool
from evolve.retrieval.alpha_evolve_adapter import retrieval_evaluation


def _candidate(code: str) -> dict:
    return {"content": {"files": [{"path": "search_tool.py", "content": code}]}}


def _scores(result: dict) -> dict:
    return {s["metric"]: s["score"] for s in result["scores"]["scores"]}


def test_baseline_candidate_scores(tmp_path):
    code = Path(search_tool.__file__).read_text(encoding="utf-8")
    result = retrieval_evaluation(_candidate(code), workdir=tmp_path, scale=1)

    scores = _scores(result)
    assert scores["combined_score"] > 0.3
    assert {"recall", "precision", "mrr", "neg_avg_tokens"} <= set(scores)
    # All reported metrics must be maximization-friendly.
    assert scores["neg_avg_tokens"] < 0


def test_broken_candidate_gets_zero_and_insights(tmp_path):
    result = retrieval_evaluation(
        _candidate("this is not python ("), workdir=tmp_path, scale=1
    )
    assert _scores(result)["combined_score"] == 0.0
    insights = result["insights"]["insights"]
    assert insights
    assert any("fail" in i["text"].lower() for i in insights)


def test_malformed_candidate_returns_sentinel(tmp_path):
    result = retrieval_evaluation({"content": {}}, workdir=tmp_path, scale=1)
    assert _scores(result)["combined_score"] <= -1e12
    assert result["insights"]["insights"]


def test_insights_report_weakest_task_kind(tmp_path):
    code = Path(search_tool.__file__).read_text(encoding="utf-8")
    result = retrieval_evaluation(_candidate(code), workdir=tmp_path, scale=1)
    labels = {i["label"] for i in result["insights"]["insights"]}
    assert any("Weakest" in label or "Token" in label for label in labels)
