"""Tests for the real-log replay harness's measurement math."""

from evolve.retrieval.real_replay import _iqr, _std, compare, observed_summary


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
    assert summary["wall_seconds_n"] == 2
    assert summary["wall_seconds_median"] == 4.0


def test_observed_summary_includes_dispersion():
    tasks = [
        _task(2, 400, 3.0),
        _task(3, 800, 0.0),
        _task(6, 2000, 5.0),
    ]
    summary = observed_summary(tasks)
    assert "tokens_iqr" in summary
    assert "tokens_std" in summary
    assert "steps_iqr" in summary
    assert len(summary["tokens_iqr"]) == 2
    assert summary["tokens_iqr"][0] <= summary["tokens_iqr"][1]


def test_iqr_basic():
    assert _iqr([1, 2, 3, 4]) == (1.5, 3.5)
    assert _iqr([10]) == (0.0, 0.0)
    assert _iqr([]) == (0.0, 0.0)


def test_std_basic():
    assert _std([10, 10, 10]) == 0.0
    assert _std([5]) == 0.0
    assert _std([]) == 0.0
    assert _std([2, 4]) > 0


def test_compare_reports_reduction_percentages():
    observed = {
        "tokens_median": 1000,
        "tokens_mean": 1200,
        "wall_seconds_median": 5.0,
        "wall_seconds_mean": 6.0,
    }
    tool = {
        "tokens_median": 250,
        "tokens_mean": 300,
        "wall_seconds_median": 0.05,
        "wall_seconds_mean": 0.06,
    }
    result = compare(tool, observed)
    assert "steps_reduction_pct" not in result
    assert result["tokens_reduction_pct"] == 75.0
    assert result["tokens_reduction_mean_pct"] == 75.0
    assert result["wall_seconds_reduction_pct"] == 99.0


def test_compare_negative_when_tool_costs_more():
    observed = {
        "tokens_median": 100, "tokens_mean": 100,
        "wall_seconds_median": 1.0, "wall_seconds_mean": 1.0,
    }
    tool = {
        "tokens_median": 400, "tokens_mean": 400,
        "wall_seconds_median": 0.1, "wall_seconds_mean": 0.1,
    }
    assert compare(tool, observed)["tokens_reduction_pct"] == -300.0
