"""Trending-2: same-list rank movement and multi-list evidence counts (descriptive metadata).

Pure functions only (no database). Movement is a factual comparison of one article's position on
one publisher list between two stored snapshots of that list; nothing here is a score.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

from industry_intelligence.services.trending import (
    list_comparison,
    native_list_evidence,
    rank_movement,
)

T0 = datetime(2033, 6, 1, 9, tzinfo=UTC)


def snapshot(entries, *, list_key="recent_hot", kind="news_ranking", window=None, **counts):
    return SimpleNamespace(
        observed_at=T0,
        result={
            "kind": kind,
            "list_key": list_key,
            "ranking_kind": "hot",
            "window": window,
            "entries": [{"rank": rank, "content_item_id": cid} for rank, cid in entries],
            "invalid_count": counts.get("invalid", 0),
            "failed_count": counts.get("failed", 0),
        },
    )


def test_movement_up_down_stable_and_new_follow_the_same_list():
    previous = {"a": 8, "b": 2, "c": 4}
    assert rank_movement(3, previous.get("a"), True) == (8, 5, "up")
    assert rank_movement(7, previous.get("b"), True) == (2, -5, "down")
    assert rank_movement(4, previous.get("c"), True) == (4, 0, "stable")
    # Absent from a valid previous snapshot of the same list.
    assert rank_movement(6, previous.get("d"), True) == (None, None, "new")


def test_no_previous_snapshot_gives_no_movement():
    assert rank_movement(6, None, False) == (None, None, None)
    assert list_comparison(snapshot([(1, "a")]), None) == (False, "no_previous_snapshot", True)


def test_absence_is_not_new_when_the_previous_snapshot_dropped_rows():
    # A rejected row in the previous snapshot may have been this article: never guess "new".
    comparable, reason, complete = list_comparison(
        snapshot([(1, "a")]), snapshot([(1, "b")], invalid=1)
    )
    assert (comparable, reason, complete) == (True, None, False)
    assert rank_movement(1, None, comparable, complete) == (None, None, None)
    assert rank_movement(2, 3, comparable, complete) == (3, 1, "up")


def test_snapshots_of_a_different_list_or_window_are_never_compared():
    current = snapshot([(1, "a")], window="day:2033-06-01")
    assert list_comparison(current, snapshot([(2, "a")], window="day:2033-05-31"))[:2] == (
        False,
        "window_changed",
    )
    other_list = snapshot([(2, "a")], list_key="other", window=current.result["window"])
    assert list_comparison(current, other_list)[:2] == (False, "list_changed")
    assert list_comparison(current, snapshot([(2, "a")], kind="ranking"))[:2] == (
        False,
        "no_previous_snapshot",
    )
    same_day = snapshot([(2, "a")], window="day:2033-06-01")
    assert list_comparison(current, same_day) == (True, None, True)


def test_native_list_evidence_counts_lists_and_publishers_separately():
    rankings = [
        {"source_key": "gematsu_popular", "source": "Gematsu", "rank": 2},
        {"source_key": "game_3dm_hot", "source": "3DM", "rank": 5},
        # A second article of the same Event on the same list is not a second list.
        {"source_key": "game_3dm_hot", "source": "3DM", "rank": 7},
    ]
    assert native_list_evidence(rankings) == {
        "native_list_count": 2,
        "native_publisher_count": 2,
        "native_publishers": ["Gematsu", "3DM"],
    }
    two_lists_one_publisher = [
        {"source_key": "x_daily", "source": "Paper", "rank": 1},
        {"source_key": "x_weekly", "source": "Paper", "rank": 3},
    ]
    assert native_list_evidence(two_lists_one_publisher) == {
        "native_list_count": 2,
        "native_publisher_count": 1,
        "native_publishers": ["Paper"],
    }
