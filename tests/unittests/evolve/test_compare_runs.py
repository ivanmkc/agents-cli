"""Tests for the run-vs-run retrieval comparison pipeline."""

from evolve.retrieval.compare_runs import aggregate, categorize_episode


def _ep(args, generator="Interactive_claude_agents-cli", motivation="orientation",
        steps=2, tokens=100):
    return {
        "calls": [("Bash", a) for a in args],
        "generator": generator,
        "motivation": motivation,
        "steps": steps,
        "tokens": tokens,
        "context": "",
        "case": "C",
        "stream": "gen0.turn0",
    }


def test_categorize_distinguishes_adk_cli_and_skill_reads():
    assert categorize_episode(
        _ep(["grep X .venv/lib/python3.12/site-packages/google/adk/agents.py"])
    ) == "adk-sdk"
    assert categorize_episode(
        _ep(["grep port /x/site-packages/google/agents/cli/run/_local_server.py"])
    ) == "tool-internals"
    assert categorize_episode(
        _ep(["cat /tmp/.claude/skills/google-agents-cli-adk-code/references/adk-python.md"])
    ) == "skill-docs"
    assert categorize_episode(_ep(["agents-cli eval --help"])) == "cli-help"
    assert categorize_episode(
        _ep(["cat agent-project/app/agent.py"])
    ) == "workspace"


def test_tool_internals_wins_over_generic_site_packages():
    ep = _ep([
        "ls /x/site-packages/google/agents/cli/",
        "cat agent-project/app/agent.py",
    ])
    assert categorize_episode(ep) == "tool-internals"


def test_aggregate_groups_by_harness_category_motivation():
    eps = [
        _ep(["cat agent-project/app/agent.py"], tokens=100),
        _ep(["cat agent-project/README.md"], tokens=50),
        _ep(["grep X /s/site-packages/google/adk/a.py"],
            generator="Interactive_gemini_agents-cli",
            motivation="failure-triggered", tokens=900),
    ]
    table = aggregate(eps, num_transcripts={"claude": 2, "gemini": 1})
    claude_ws = table[("claude", "workspace", "orientation")]
    assert claude_ws["episodes"] == 2
    assert claude_ws["tokens"] == 150
    gemini_sdk = table[("gemini", "adk-sdk", "failure-triggered")]
    assert gemini_sdk["episodes"] == 1
    assert gemini_sdk["tokens"] == 900


def test_agy_antigravity_skill_path_categorized_as_skill_docs():
    """C8: AGY reads skills from ``.gemini/antigravity-cli/skills/``."""
    ep = _ep(
        ["cat /work/.gemini/antigravity-cli/skills/deploy-guide/reference.md"]
    )
    assert categorize_episode(ep) == "skill-docs"
