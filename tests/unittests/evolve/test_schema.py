"""Tests for the generic (framework-agnostic) real-log eval manifest schema."""

import json

import pytest

from evolve.retrieval.schema import (
    CorpusDecl,
    CorpusRole,
    EvalTask,
    Observed,
    PackagePin,
    RealEvalManifest,
    SchemaError,
    Span,
)


def _manifest_dict():
    return {
        "schema_version": 1,
        "provenance": {
            "source_run": "2026-04-13_18-02-34_daily",
            "miner": "evolve.retrieval.log_mining",
            "validation": "validation/consensus_report.json",
        },
        "corpus_store": "corpus",
        "corpora": {
            "adk-sdk": {
                "role": "dependency",
                "pin": {"kind": "package", "name": "google-adk",
                        "version": "1.34.1", "subpath": "google/adk"},
            },
            "agent-project": {
                "role": "workspace",
                "pin": {"kind": "command",
                        "command": "agents-cli scaffold create agent-project -y -s",
                        "tool_version": "google-agents-cli==0.3.0"},
            },
        },
        "tasks": [
            {
                "id": "r0017",
                "family": "dependency-symbol",
                "corpus": "adk-sdk",
                "query": "find the LlmAgent definition",
                "expected_spans": [
                    {"file": "agents/llm_agent.py", "start_line": 40, "end_line": 95}
                ],
                "pins": {"files": {"agents/llm_agent.py": "ab" * 32}},
                "observed": {"steps": 3, "tokens": 3348, "wall_seconds": 6.3,
                             "agent": "Interactive_claude_agents-cli",
                             "case": "CLI-DEV-001"},
                "validation": {"consensus": "valid", "unanimous": True,
                               "issues": []},
            }
        ],
    }


def test_manifest_roundtrips_through_json():
    manifest = RealEvalManifest.from_dict(_manifest_dict())
    again = RealEvalManifest.from_dict(json.loads(manifest.to_json()))
    assert again == manifest
    assert again.tasks[0].corpus == "adk-sdk"
    assert again.corpora["adk-sdk"].role is CorpusRole.DEPENDENCY
    assert isinstance(again.corpora["adk-sdk"].pin, PackagePin)


def test_task_corpus_must_be_declared():
    data = _manifest_dict()
    data["tasks"][0]["corpus"] = "undeclared"
    with pytest.raises(SchemaError, match="undeclared"):
        RealEvalManifest.from_dict(data)


def test_span_rejects_inverted_line_range():
    with pytest.raises(SchemaError, match="start_line"):
        Span(file="a.py", start_line=9, end_line=3)


def test_evaluator_dict_adds_kind_as_derived_view():
    # LocalEvaluator groups by "kind"; the manifest stores only "family".
    manifest = RealEvalManifest.from_dict(_manifest_dict())
    row = manifest.tasks[0].to_evaluator_dict()
    assert row["kind"] == "dependency-symbol"
    assert row["expected_spans"] == [
        {"file": "agents/llm_agent.py", "start_line": 40, "end_line": 95}
    ]
    # The persisted form must NOT contain the derived duplicate.
    assert "kind" not in json.loads(manifest.to_json())["tasks"][0]


def test_unsupported_schema_version_raises():
    data = _manifest_dict()
    data["schema_version"] = 99
    with pytest.raises(SchemaError, match="schema_version"):
        RealEvalManifest.from_dict(data)


def test_unknown_pin_kind_raises():
    data = _manifest_dict()
    data["corpora"]["adk-sdk"]["pin"] = {"kind": "carrier-pigeon"}
    with pytest.raises(SchemaError, match="carrier-pigeon"):
        RealEvalManifest.from_dict(data)


def test_task_helpers_expose_typed_fields():
    task = EvalTask(
        id="r1",
        family="workspace-file",
        corpus="agent-project",
        query="look at the agent file",
        expected_spans=(Span(file="app/agent.py", start_line=1, end_line=10),),
        pins={"app/agent.py": "cd" * 32},
        observed=Observed(steps=2, tokens=100, wall_seconds=1.0,
                          agent="x", case="y"),
    )
    assert task.validation is None
    assert task.to_evaluator_dict()["query"] == "look at the agent file"


def test_corpus_decl_role_is_enum_not_string():
    decl = CorpusDecl.from_dict(
        {"role": "workspace",
         "pin": {"kind": "command", "command": "make corpus", "tool_version": ""}}
    )
    assert decl.role is CorpusRole.WORKSPACE


def test_task_motivation_roundtrips():
    data = _manifest_dict()
    data["tasks"][0]["motivation"] = "failure-triggered"
    manifest = RealEvalManifest.from_dict(data)
    assert manifest.tasks[0].motivation == "failure-triggered"
    assert json.loads(manifest.to_json())["tasks"][0]["motivation"] == "failure-triggered"


def test_task_motivation_optional():
    manifest = RealEvalManifest.from_dict(_manifest_dict())
    assert manifest.tasks[0].motivation is None
    assert "motivation" not in json.loads(manifest.to_json())["tasks"][0]
