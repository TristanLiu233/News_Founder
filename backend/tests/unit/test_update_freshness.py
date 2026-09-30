"""Deployment-2A freshness rule: a success that started within the window is fresh."""

from datetime import UTC, datetime, timedelta

from industry_intelligence.services.update_freshness import (
    FRESH_STATUSES,
    FRESHNESS_MAX_AGE,
    is_fresh,
    render_text,
)

NOW = datetime(2034, 5, 2, 2, 30, tzinfo=UTC)


def test_window_is_26_hours_and_only_success_counts():
    assert FRESHNESS_MAX_AGE == timedelta(hours=26)
    assert FRESH_STATUSES == ("success",)


def test_boundaries_are_inclusive_and_future_runs_do_not_count():
    assert is_fresh(NOW - timedelta(hours=26), NOW)
    assert is_fresh(NOW, NOW)
    assert not is_fresh(NOW - timedelta(hours=26, seconds=1), NOW)
    assert not is_fresh(NOW + timedelta(seconds=1), NOW)
    assert not is_fresh(None, NOW)
    assert is_fresh(NOW - timedelta(hours=30), NOW, timedelta(hours=48))


def test_text_report_without_history():
    text = render_text(
        {
            "status": "stale",
            "checked_at": NOW.isoformat(),
            "max_age_hours": 26.0,
            "fresh_statuses": ["success"],
            "last_success": None,
            "last_success_age_hours": None,
            "latest_run": None,
        }
    )
    assert text.splitlines()[0] == "Update freshness: STALE"
    assert "last success: none recorded" in text
    assert "latest run: none recorded" in text
