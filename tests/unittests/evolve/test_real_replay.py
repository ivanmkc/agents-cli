"""Tests for the real-log replay harness's measurement math."""

from evolve.retrieval.real_replay import compare, observed_summary


def _task(steps, tokens, wall, family="workspace-file"):
    return {
        "family": family,
        "observed": {
            "steps": steps, "tokens": tokens, "wall_seconds": wall,
        },
    }


def test_observed_summary_medians_and_zero_wall_excluded():
    tasks = [
        _task(2, 400, 3.0),
        _task(3, 800, 0.0),   # missing timestamps -> excluded from wall
        _task(6, 2000, 5.0),
    ]
    summary = observed_summary(tasks)
    assert summary["num_cases"] == 3
    assert summary["steps_median"] == 3
    assert summary["tokens_median"] == 800
    # 0-second spans are missing data, not instant searches.
    assert summary["wall_seconds_n"] == 2
    assert summary["wall_seconds_median"] == 4.0


def test_compare_reports_reduction_percentages():
    observed = {
        "steps_median": 4,
        "tokens_median": 1000,
        "wall_seconds_median": 5.0,
    }
    tool = {
        "steps_median": 1,
        "tokens_median": 250,
        "wall_seconds_median": 0.05,
    }
    result = compare(tool, observed)
    assert result["steps_reduction_pct"] == 75.0
    assert result["tokens_reduction_pct"] == 75.0
    assert result["wall_seconds_reduction_pct"] == 99.0


def test_compare_negative_when_tool_costs_more():
    observed = {
        "steps_median": 2, "tokens_median": 100, "wall_seconds_median": 1.0,
    }
    tool = {
        "steps_median": 1, "tokens_median": 400, "wall_seconds_median": 0.1,
    }
    assert compare(tool, observed)["tokens_reduction_pct"] == -300.0
