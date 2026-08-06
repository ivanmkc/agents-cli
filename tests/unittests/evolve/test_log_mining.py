"""Tests for mining retrieval episodes out of agent-generator transcripts.

The miner turns an ``agent_events`` stream into *retrieval episodes*:
maximal runs of consecutive information-seeking tool calls (reads,
greps, globs, ls, and read-only shell commands), ended by an action
(edit/write/install/run). Each episode carries the real measured cost:
wall-clock seconds, token estimate (chars/4, matching the token-
economics book's convention), and step count.
"""

from evolve.retrieval.log_mining import (
    classify_tool_call,
    segment_episodes,
)


def _ev(tool, tool_input, ts, output="", dur=100):
    """Build a tool_use + tool_result event pair like the real records."""
    return [
        {
            "type": "tool_use",
            "tool_name": tool,
            "tool_input": tool_input,
            "timestamp": ts,
            "duration_ms": dur,
            "tool_call_id": f"c{ts}",
        },
        {
            "type": "tool_result",
            "tool_name": tool,
            "tool_output": output,
            "timestamp": ts + 0.1,
            "tool_call_id": f"c{ts}",
        },
    ]


def _msg(text, ts):
    return [{"type": "message", "role": "assistant", "content": text, "timestamp": ts}]


def test_classify_direct_retrieval_tools_across_harnesses():
    # Claude names
    assert classify_tool_call("Read", {"file_path": "x.py"}) == "retrieval"
    assert classify_tool_call("Grep", {"pattern": "foo"}) == "retrieval"
    # Gemini names
    assert classify_tool_call("read_file", {"path": "x.py"}) == "retrieval"
    assert classify_tool_call("grep_search", {"query": "foo"}) == "retrieval"
    assert classify_tool_call("list_directory", {"path": "."}) == "retrieval"
    assert classify_tool_call("glob", {"pattern": "*.py"}) == "retrieval"
    # AGY names
    assert classify_tool_call("list_dir", {"path": "."}) == "retrieval"


def test_classify_shell_commands_by_leading_program():
    for cmd in (
        "cat /usr/lib/python3/site-packages/google/adk/agents.py",
        "grep -rn root_agent app/",
        "find / -name '*.py' | head",
        "ls -la app",
        "head -50 pyproject.toml",
        "pip show google-adk",
        "pip list | grep -i adk",
        "which adk",
    ):
        assert classify_tool_call("Bash", {"command": cmd}) == "retrieval", cmd
    for cmd in (
        "pip install google-adk",
        "uv run python app/agent.py",
        "mkdir -p app",
        "rm -rf build",
        "python3 setup.py install",
    ):
        assert classify_tool_call("Bash", {"command": cmd}) == "action", cmd
    # Gemini's shell name behaves identically
    assert classify_tool_call("run_shell_command", {"command": "cat x.py"}) == "retrieval"


def test_classify_action_and_neutral_tools():
    assert classify_tool_call("Edit", {}) == "action"
    assert classify_tool_call("Write", {}) == "action"
    assert classify_tool_call("write_file", {}) == "action"
    assert classify_tool_call("replace", {}) == "action"
    assert classify_tool_call("TodoWrite", {}) == "neutral"
    assert classify_tool_call("write_todos", {}) == "neutral"


def test_segment_episodes_basic():
    """Three reads then an edit -> one 3-step episode ended by the edit."""
    events = (
        _msg("I need to find where root_agent is defined", 1.0)
        + _ev("Read", {"file_path": "app/agent.py"}, 2.0, output="x" * 400)
        + _ev("Bash", {"command": "grep -rn root_agent app/"}, 3.0, output="y" * 400)
        + _ev("Read", {"file_path": "app/tools.py"}, 4.0, output="z" * 200)
        + _ev("Edit", {"file_path": "app/agent.py"}, 5.0)
    )
    eps = segment_episodes(events)
    assert len(eps) == 1
    ep = eps[0]
    assert ep["steps"] == 3
    assert ep["ended_by"] == "Edit"
    # Wall time spans first retrieval call to its last result.
    assert ep["wall_seconds"] > 0
    # Tokens ~ chars/4 of inputs+outputs of the 3 retrieval calls.
    assert ep["tokens"] > (400 + 400 + 200) / 4
    # The info-need is the message right before the episode.
    assert "root_agent" in ep["context"]
    assert [t for t, _ in ep["calls"]] == ["Read", "Bash", "Read"]


def test_segment_episodes_survive_messages_break_on_actions():
    """Messages between reads don't split an episode; actions do."""
    events = (
        _ev("Read", {"file_path": "a.py"}, 1.0)
        + _msg("hmm, let me check b too", 1.5)
        + _ev("Read", {"file_path": "b.py"}, 2.0)
        + _ev("Write", {"file_path": "c.py"}, 3.0)
        + _ev("Read", {"file_path": "d.py"}, 4.0)
    )
    eps = segment_episodes(events)
    assert len(eps) == 2
    assert eps[0]["steps"] == 2
    assert eps[1]["steps"] == 1
    assert eps[1]["ended_by"] is None  # transcript ended mid-search


def test_iso_timestamps_yield_wall_seconds():
    """CI runs use ISO-8601 timestamp strings; wall time must still work."""
    events = [
        {"type": "tool_use", "tool_name": "Read", "tool_input": {"file_path": "a.py"},
         "timestamp": "2026-04-13T18:15:51.580Z", "duration_ms": 4604.0,
         "tool_call_id": "c1"},
        {"type": "tool_result", "tool_name": "Read", "tool_output": "x" * 100,
         "timestamp": "2026-04-13T18:15:56.184Z", "tool_call_id": "c1"},
        {"type": "tool_use", "tool_name": "Grep", "tool_input": {"pattern": "p"},
         "timestamp": "2026-04-13T18:16:03.000Z", "duration_ms": 900.0,
         "tool_call_id": "c2"},
        {"type": "tool_result", "tool_name": "Grep", "tool_output": "y",
         "timestamp": "2026-04-13T18:16:04.100Z", "tool_call_id": "c2"},
        {"type": "tool_use", "tool_name": "Edit", "tool_input": {},
         "timestamp": "2026-04-13T18:16:10.000Z", "tool_call_id": "c3"},
    ]
    eps = segment_episodes(events)
    assert len(eps) == 1
    # 18:15:51.580 -> 18:16:04.100 = 12.52s
    assert 12.0 < eps[0]["wall_seconds"] < 13.0


def test_min_steps_filter():
    """Single-read episodes can be filtered out (routine, not search)."""
    events = (
        _ev("Read", {"file_path": "a.py"}, 1.0)
        + _ev("Edit", {"file_path": "a.py"}, 2.0)
        + _ev("Read", {"file_path": "b.py"}, 3.0)
        + _ev("Bash", {"command": "cat c.py"}, 4.0)
        + _ev("Bash", {"command": "grep foo d.py"}, 5.0)
        + _ev("Edit", {"file_path": "b.py"}, 6.0)
    )
    eps = segment_episodes(events, min_steps=2)
    assert len(eps) == 1
    assert eps[0]["steps"] == 3
