"""Tests for the agent-in-the-loop lift harness (pure parts only —
live agent runs are exercised manually, not in unit tests)."""

from evolve.retrieval.agent_lift import (
    extract_answer,
    is_correct,
    sample_tasks,
)


def test_extract_answer_finds_json_in_chatter():
    text = 'Sure! Here is the answer:\n```json\n{"file": "packages/auth/module_0.py", "start_line": 3, "end_line": 20}\n```\nDone.'
    answer = extract_answer(text)
    assert answer == {
        "file": "packages/auth/module_0.py",
        "start_line": 3,
        "end_line": 20,
    }


def test_extract_answer_handles_garbage():
    assert extract_answer("I could not find it, sorry") is None
    assert extract_answer('{"file": "x.py"}') is None  # missing lines


def test_is_correct_requires_file_and_overlap():
    spans = [{"file": "a.py", "start_line": 10, "end_line": 20}]
    assert is_correct({"file": "a.py", "start_line": 15, "end_line": 25}, spans)
    assert not is_correct({"file": "b.py", "start_line": 15, "end_line": 25}, spans)
    assert not is_correct({"file": "a.py", "start_line": 30, "end_line": 40}, spans)


def test_sample_tasks_is_deterministic_and_stratified():
    tasks = (
        [{"id": f"d{i}", "kind": "definition"} for i in range(50)]
        + [{"id": f"u{i}", "kind": "usage"} for i in range(20)]
        + [{"id": f"c{i}", "kind": "config"} for i in range(10)]
    )
    picked_a = sample_tasks(tasks, n=9, seed=1)
    picked_b = sample_tasks(tasks, n=9, seed=1)
    assert picked_a == picked_b
    assert len(picked_a) == 9
    kinds = {t["kind"] for t in picked_a}
    assert kinds == {"definition", "usage", "config"}
