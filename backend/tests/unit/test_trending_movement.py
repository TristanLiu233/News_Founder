"""Trending-2: same-list rank movement, multi-list evidence counts, and the Home selection rules.

Pure functions only (no database). Movement is a factual comparison of one article's position on
one publisher list between two stored snapshots of that list; nothing here is a score.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from industry_intelligence.services.trending import (
    HOME_MAX_REPORT_AGE,
    home_eligible,
    home_round_robin,
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


def test_home_round_robin_alternates_lists_in_native_order():
    finance = [["s1", "s2", "s3"], ["e1", "e2", "e3"]]
    assert home_round_robin(finance, 4) == ["s1", "e1", "s2", "e2"]
    # Computer has one list: its native order is kept.
    assert home_round_robin([["h1", "h2", "h3", "h4", "h5"]], 4) == ["h1", "h2", "h3", "h4"]


def test_home_round_robin_shows_a_shared_event_once_and_keeps_filling():
    # Sina #1 and EastMoney #2 are the same Event X.
    queues = [["X", "s2", "s3"], ["e1", "X", "e3"]]
    assert home_round_robin(queues, 4) == ["X", "e1", "s2", "e3"]
    # A shorter list simply runs out; the others continue in turn.
    assert home_round_robin([["g1"], ["d1", "d2", "d3"]], 4) == ["g1", "d1", "d2", "d3"]
    assert home_round_robin([["a"], ["a"]], 4) == ["a"]
    assert home_round_robin([], 4) == []
    assert home_round_robin([["a", "b"], ["c"]], None) == ["a", "c", "b"]


def test_home_eligibility_measures_report_age_at_list_observation():
    assert HOME_MAX_REPORT_AGE == timedelta(hours=48)
    observed = [T0]
    assert home_eligible(T0 - timedelta(hours=47, minutes=59), observed)
    assert home_eligible(T0 - timedelta(hours=48), observed)
    assert not home_eligible(T0 - timedelta(hours=48, seconds=1), observed)
    # A report newer than the snapshot (a later report joined the Event) is current.
    assert home_eligible(T0 + timedelta(hours=1), observed)
    # Listed on two lists: age is measured at the more recent observation (the stricter one).
    two = [T0 - timedelta(hours=30), T0]
    assert home_eligible(T0 - timedelta(hours=40), two)
    assert not home_eligible(T0 - timedelta(hours=60), two)
    assert not home_eligible(None, observed)
