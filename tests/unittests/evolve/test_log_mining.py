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
    eps = segment_episodes(events, min_steps=1)
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


def test_streamed_message_chunks_join_into_episode_context():
    # Gemini streams one utterance as several message events; the episode
    # context must be the whole utterance, not the final fragment.
    events = (
        _msg("I will check the unit tests by listing the", 1.0)
        + _msg(" files in the `tests/unit` directory.", 1.1)
        + _ev("list_directory", {"path": "tests/unit"}, 2.0)
        + _ev("read_file", {"path": "tests/unit/test_agent.py"}, 3.0)
    )
    eps = segment_episodes(events, min_steps=2)
    assert len(eps) == 1
    assert eps[0]["context"] == (
        "I will check the unit tests by listing the"
        " files in the `tests/unit` directory."
    )


def test_context_survives_intervening_tool_call():
    # A complete utterance stays available as context even when a tool
    # call happens between it and the episode start.
    events = (
        _msg("I will inspect the failing test setup.", 1.0)
        + _ev("Bash", {"command": "pip install foo"}, 2.0)
        + _ev("Grep", {"pattern": "fixture"}, 3.0)
        + _ev("Read", {"file_path": "tests/conftest.py"}, 4.0)
    )
    eps = segment_episodes(events, min_steps=2)
    assert len(eps) == 1
    assert eps[0]["context"] == "I will inspect the failing test setup."


def test_reads_of_files_the_agent_wrote_are_not_retrieval():
    # Re-reading a file the agent itself just wrote is verification,
    # not search; such calls must not form episodes.
    events = (
        _ev("Write", {"file_path": "app/agent.py", "content": "x = 1"}, 1.0)
        + _ev("Read", {"file_path": "app/agent.py"}, 2.0)
        + _ev("Read", {"file_path": "app/agent.py"}, 3.0)
    )
    assert segment_episodes(events, min_steps=1) == []


def test_verification_reads_excluded_from_mixed_episode():
    events = (
        _ev("Write", {"file_path": "app/agent.py", "content": "x = 1"}, 1.0)
        + _ev("Read", {"file_path": "app/agent.py"}, 2.0)  # verification
        + _ev("Read", {"file_path": "app/server.py"}, 3.0)  # genuine
        + _ev("Grep", {"pattern": "root_agent"}, 4.0)  # genuine
    )
    eps = segment_episodes(events, min_steps=1)
    assert len(eps) == 1
    assert eps[0]["steps"] == 2
    assert all("agent.py" not in summary for _, summary in eps[0]["calls"])


def test_verification_read_matches_relative_vs_absolute_paths():
    events = (
        _ev("write_file", {"path": "/work/app/agent.py", "content": "x"}, 1.0)
        + _ev("read_file", {"path": "app/agent.py"}, 2.0)
    )
    assert segment_episodes(events, min_steps=1) == []


def test_shell_read_of_own_write_is_verification():
    events = (
        _ev("Write", {"file_path": "app/agent.py", "content": "x = 1"}, 1.0)
        + _ev("Bash", {"command": "cat app/agent.py"}, 2.0)
    )
    assert segment_episodes(events, min_steps=1) == []


def _failing_ev(tool, tool_input, ts):
    """Tool call whose result carries an error signature."""
    pair = _ev(tool, tool_input, ts)
    pair[1]["tool_output"] = (
        "Traceback (most recent call last):\n  ...\nConnectionError: port 18085 refused"
    )
    return pair


def test_episode_after_failing_result_is_failure_triggered():
    events = (
        _failing_ev("Bash", {"command": "agents-cli run 'hi'"}, 1.0)
        + _ev("Grep", {"pattern": "port"}, 2.0)
        + _ev("Read", {"file_path": "src/_local_server.py"}, 3.0)
    )
    eps = segment_episodes(events, min_steps=2)
    assert len(eps) == 1
    assert eps[0]["motivation"] == "failure-triggered"


def test_mid_stream_episode_ending_in_write_is_pre_write_verification():
    # Deep in the stream (past the orientation window), search -> write
    # is API/pre-write verification.
    prefix = []
    for i in range(6):
        prefix += _ev("Write", {"file_path": f"app/f{i}.py", "content": "x"},
                      float(i + 1))
    events = (
        prefix
        + _ev("Read", {"file_path": "app/other.py"}, 10.0)
        + _ev("Grep", {"pattern": "root_agent"}, 11.0)
        + _ev("Write", {"file_path": "app/new.py", "content": "x"}, 12.0)
    )
    eps = segment_episodes(events, min_steps=2)
    assert len(eps) == 1
    assert eps[0]["motivation"] == "pre-write-verification"


def test_early_stream_episode_is_orientation_even_before_a_write():
    # The first searches after scaffolding are orientation sweeps, even
    # though the agent writes right afterwards.
    events = (
        _ev("Read", {"file_path": "README.md"}, 1.0)
        + _ev("Glob", {"pattern": "**/*.py"}, 2.0)
        + _ev("Write", {"file_path": "app/agent.py", "content": "x"}, 3.0)
    )
    eps = segment_episodes(events, min_steps=2)
    assert eps[0]["motivation"] == "orientation"


def test_unprompted_search_at_stream_end_is_orientation():
    events = (
        _ev("Read", {"file_path": "README.md"}, 1.0)
        + _ev("Grep", {"pattern": "install"}, 2.0)
    )
    eps = segment_episodes(events, min_steps=2)
    assert len(eps) == 1
    assert eps[0]["motivation"] == "orientation"


def test_failure_trigger_outranks_pre_write_ending():
    events = (
        _failing_ev("Bash", {"command": "uv run pytest"}, 1.0)
        + _ev("Read", {"file_path": "tests/conftest.py"}, 2.0)
        + _ev("Grep", {"pattern": "fixture"}, 3.0)
        + _ev("Edit", {"file_path": "tests/conftest.py"}, 4.0)
    )
    eps = segment_episodes(events, min_steps=2)
    assert eps[0]["motivation"] == "failure-triggered"


def test_skill_injection_tokens_counts_payload_after_skill_call():
    from evolve.retrieval.log_mining import skill_injection_tokens

    events = (
        _msg("Let me check the reference.", 1.0)
        + [{"type": "tool_use", "tool_name": "Skill",
            "tool_input": {"skill": "google-agents-cli-adk-code"},
            "timestamp": 2.0, "tool_call_id": "s1"}]
        + [{"type": "tool_result", "tool_name": "Skill",
            "tool_output": "Launching skill: google-agents-cli-adk-code",
            "timestamp": 2.1, "tool_call_id": "s1"}]
        + _msg("X" * 4000, 2.2)  # the injected reference payload
        + _ev("Read", {"file_path": "app/agent.py"}, 3.0)
    )
    assert skill_injection_tokens(events) == 1000  # 4000 chars / 4


def test_skill_injection_tokens_zero_without_skill_calls():
    from evolve.retrieval.log_mining import skill_injection_tokens

    events = _msg("hello", 1.0) + _ev("Read", {"file_path": "a.py"}, 2.0)
    assert skill_injection_tokens(events) == 0


# --- C4c: _FAILURE_SIGNATURE false-positive reduction ---

def test_failure_signature_does_not_match_error_in_compound_identifiers():
    """Compound identifiers like 'error_code' in source code must not trigger."""
    events = (
        _ev("Bash", {"command": "uv run pytest"}, 1.0)
        + _ev("Read", {"file_path": "app/utils.py"}, 2.0)
        + _ev("Grep", {"pattern": "handler"}, 3.0)
        + _ev("Edit", {"file_path": "app/utils.py"}, 4.0)
    )
    # Patch the action result to contain compound identifiers with "error"
    # as a substring — word boundaries must prevent these from matching.
    events[1]["tool_output"] = (
        "def handle_request(req):\n"
        "    error_code = req.get_error_code()\n"
        "    return ErrorResponse(error_code)\n"
    )
    eps = segment_episodes(events, min_steps=2)
    assert len(eps) == 1
    # The action (uv run pytest) did not fail — its output only has compound identifiers
    assert eps[0]["motivation"] != "failure-triggered"


def test_failure_signature_does_not_match_error_class():
    """'ErrorClass' or 'error_handler' must not trigger failure signature."""
    events = (
        _ev("Bash", {"command": "uv run pytest"}, 1.0)
        + _ev("Read", {"file_path": "app/errors.py"}, 2.0)
        + _ev("Grep", {"pattern": "ErrorClass"}, 3.0)
        + _ev("Edit", {"file_path": "app/errors.py"}, 4.0)
    )
    # The action result just contains class names with "error" as a substring
    events[1]["tool_output"] = (
        "class ErrorClass:\n    pass\n\n"
        "def error_handler():\n    pass\n"
    )
    eps = segment_episodes(events, min_steps=2)
    assert len(eps) == 1
    assert eps[0]["motivation"] != "failure-triggered"


def test_failure_signature_matches_gemini_exit_code_format():
    """'Exit Code: 1' (Gemini format with capital E and colon) must trigger."""
    from evolve.retrieval.log_mining import _FAILURE_SIGNATURE

    assert _FAILURE_SIGNATURE.search("Exit Code: 1") is not None


def test_failure_signature_still_matches_real_errors():
    """Standalone 'error' as a whole word still triggers (e.g. 'an error occurred')."""
    from evolve.retrieval.log_mining import _FAILURE_SIGNATURE

    assert _FAILURE_SIGNATURE.search("an error occurred") is not None
    assert _FAILURE_SIGNATURE.search("command failed") is not None
    assert _FAILURE_SIGNATURE.search("Traceback (most recent call last):") is not None
    assert _FAILURE_SIGNATURE.search("exit code 1") is not None
    assert _FAILURE_SIGNATURE.search("connection refused") is not None


# --- C4d: Lookback depth ---

def test_failure_two_actions_back_still_triggers():
    """A failure 2 actions back (within lookback=3) triggers failure motivation."""
    events = (
        # Action 1: fails
        _failing_ev("Bash", {"command": "uv run pytest"}, 1.0)
        # Action 2: a narration + neutral tool (pushes failure back)
        + _msg("Let me check something first", 2.0)
        + _ev("Write", {"file_path": "app/note.py", "content": "x"}, 3.0)
        # Now retrieval episode starts — the failure is 2 actions back
        + _ev("Grep", {"pattern": "fixture"}, 4.0)
        + _ev("Read", {"file_path": "tests/conftest.py"}, 5.0)
        + _ev("Edit", {"file_path": "tests/conftest.py"}, 6.0)
    )
    eps = segment_episodes(events, min_steps=2)
    assert len(eps) == 1
    assert eps[0]["motivation"] == "failure-triggered"


def test_failure_four_actions_back_does_not_trigger():
    """A failure 4+ actions back (outside lookback=3) does not trigger."""
    from evolve.retrieval.log_mining import _FAILURE_LOOKBACK

    events = _failing_ev("Bash", {"command": "uv run pytest"}, 1.0)
    # Add enough non-failing actions to push the failure outside the window
    for i in range(_FAILURE_LOOKBACK):
        events += _ev("Write", {"file_path": f"app/f{i}.py", "content": "x"},
                       float(10 + i))
    # Now a retrieval episode starts — failure is outside the window
    events += (
        _ev("Grep", {"pattern": "fixture"}, 20.0)
        + _ev("Read", {"file_path": "tests/conftest.py"}, 21.0)
        + _ev("Edit", {"file_path": "tests/conftest.py"}, 22.0)
    )
    eps = segment_episodes(events, min_steps=2)
    assert len(eps) == 1
    # Should be pre-write-verification or orientation, NOT failure-triggered
    assert eps[0]["motivation"] != "failure-triggered"


# --- C3: Redirect-write misclassification ---


def test_redirect_write_cat_heredoc_is_action():
    """cat > file << 'EOF' is a file write, not retrieval."""
    assert classify_tool_call(
        "Bash", {"command": "cat > /tmp/test.py << 'EOF'\nprint('hi')\nEOF"}
    ) == "action"


def test_redirect_append_is_action():
    """echo hello >> agent.py is a file append, not retrieval."""
    assert classify_tool_call(
        "Bash", {"command": "echo hello >> agent.py"}
    ) == "action"


def test_stderr_redirect_is_still_readonly():
    """2>/dev/null is stderr redirect, should not trigger action."""
    assert classify_tool_call(
        "Bash", {"command": "cat file 2>/dev/null"}
    ) == "retrieval"


def test_stderr_dup_redirect_is_still_readonly():
    """2>&1 is stderr dup, should not trigger action."""
    assert classify_tool_call(
        "Bash", {"command": "grep foo bar 2>&1"}
    ) == "retrieval"


def test_stdout_redirect_in_pipeline_is_action():
    """Any stage with stdout redirect to file makes the whole command action."""
    assert classify_tool_call(
        "Bash", {"command": "ls -la | tee > output.txt"}
    ) == "action"


# --- C4: Quote-blind shell splitting ---


def test_quoted_pipe_in_grep_is_readonly():
    """grep -E 'a|b' file should NOT split on the | inside quotes."""
    assert classify_tool_call(
        "Bash", {"command": 'grep -E "a|b" file'}
    ) == "retrieval"


def test_quoted_pipe_in_tree_is_readonly():
    """tree -I '.venv|__pycache__' should NOT split on the | inside quotes."""
    assert classify_tool_call(
        "Bash", {"command": 'tree -I ".venv|__pycache__"'}
    ) == "retrieval"


def test_single_quoted_pipe_is_readonly():
    """Single-quoted pipes should also be respected."""
    assert classify_tool_call(
        "Bash", {"command": "grep -E 'foo|bar|baz' README.md"}
    ) == "retrieval"


def test_real_pipe_still_works():
    """A real pipe between two readonly programs is still retrieval."""
    assert classify_tool_call(
        "Bash", {"command": "find . -name '*.py' | grep test"}
    ) == "retrieval"


# --- C13: sed -n dead entry ---


def test_sed_n_readonly():
    """sed -n (print-only) should be classified as retrieval."""
    assert classify_tool_call(
        "Bash", {"command": "sed -n '5,10p' file.py"}
    ) == "retrieval"


def test_sed_plain_readonly():
    """Plain sed without -i is informational (readonly)."""
    assert classify_tool_call(
        "Bash", {"command": "sed 's/old/new/' file.py"}
    ) == "retrieval"


def test_sed_inplace_is_action():
    """sed -i (in-place edit) is a write action."""
    assert classify_tool_call(
        "Bash", {"command": "sed -i 's/old/new/' file.py"}
    ) == "action"


def test_sed_inplace_with_backup_is_action():
    """sed -i.bak (in-place with backup) is also a write action."""
    assert classify_tool_call(
        "Bash", {"command": "sed -i.bak 's/old/new/' file.py"}
    ) == "action"


# --- C14: min_steps default inconsistency ---


def test_segment_episodes_default_min_steps_is_two():
    """Default min_steps should be 2, filtering out single-step episodes."""
    events = (
        _ev("Read", {"file_path": "a.py"}, 1.0)
        + _ev("Edit", {"file_path": "a.py"}, 2.0)
    )
    # With default min_steps (should be 2), a single-step episode is dropped.
    eps = segment_episodes(events)
    assert len(eps) == 0, (
        f"Expected 0 episodes with default min_steps=2, got {len(eps)}"
    )


def test_activate_skill_triggers_skill_injection_tokens():
    """C7: Gemini's ``activate_skill`` tool must be recognized as a skill call."""
    from evolve.retrieval.log_mining import skill_injection_tokens

    events = (
        [{"type": "tool_use", "tool_name": "activate_skill",
          "tool_input": {"skill": "google-agents-cli-adk-code"},
          "timestamp": 1.0, "tool_call_id": "s1"}]
        + [{"type": "tool_result", "tool_name": "activate_skill",
            "tool_output": "Launching skill",
            "timestamp": 1.1, "tool_call_id": "s1"}]
        + _msg("Y" * 2000, 1.2)  # injected payload
    )
    assert skill_injection_tokens(events) == 500  # 2000 chars / 4


def test_read_url_content_classified_as_retrieval():
    """C16: AGY's ``read_url_content`` tool must be classified as retrieval."""
    assert classify_tool_call("read_url_content", {"url": "https://example.com"}) == "retrieval"


def test_agy_run_command_with_commandline_key():
    """C15: AGY stores shell commands in ``CommandLine``, not ``command``."""
    assert classify_tool_call(
        "run_command", {"CommandLine": "cat /etc/os-release"}
    ) == "retrieval"
    assert classify_tool_call(
        "run_command", {"CommandLine": "pip install foo"}
    ) == "action"
