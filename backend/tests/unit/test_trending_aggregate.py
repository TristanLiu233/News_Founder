"""Trending-3: the aggregate presentation order of canonical Events (pure functions, no database).

The order is derived only from current publisher-native ranks: the best native rank on one shared
depth scale plus a small, capped bonus per additional independent publisher. It is a presentation
order, not a popularity score. Cases A–F follow the phase brief; G and H (canonical dedupe and
separation) need Events and are PostgreSQL tests in ``test_trending_postgres.py``.
"""

from datetime import UTC, datetime, timedelta
from fractions import Fraction

from industry_intelligence.services.trending import (
    MAX_BONUS_PUBLISHERS,
    MAX_REPORT_AGE,
    PUBLISHER_BONUS,
    REFERENCE_DEPTH,
    aggregate_order,
    current_eligible,
    ordering_value,
    publisher_bonus,
    rank_signal,
)

T0 = datetime(2033, 6, 1, 9, tzinfo=UTC)


def order(*candidates):
    """candidates: (event_id, best rank, publishers[, latest report])."""

    return aggregate_order((c[0], c[1], c[2], c[3] if len(c) > 3 else T0) for c in candidates)


def test_rank_signal_is_one_shared_bounded_monotonic_scale():
    assert REFERENCE_DEPTH == 30
    assert rank_signal(1) == 1
    assert rank_signal(2) == Fraction(29, 30)
    assert rank_signal(30) == Fraction(1, 30)
    # Deeper than the reference depth: the floor, never zero or negative.
    assert rank_signal(31) == rank_signal(500) == Fraction(1, 30)
    values = [rank_signal(rank) for rank in range(1, 31)]
    assert values == sorted(values, reverse=True) and len(set(values)) == 30
    assert all(0 < value <= 1 for value in values)


def test_publisher_bonus_counts_additional_publishers_and_is_capped():
    assert PUBLISHER_BONUS == Fraction(1, 10) and MAX_BONUS_PUBLISHERS == 2
    assert publisher_bonus(0) == publisher_bonus(1) == 0
    assert publisher_bonus(2) == Fraction(1, 10)
    assert publisher_bonus(3) == publisher_bonus(9) == Fraction(2, 10)
    # One additional publisher is worth exactly three places on the shared scale.
    assert ordering_value(5, 2) == ordering_value(2, 1)


def test_case_a_single_source_follows_the_native_rank():
    assert order(("C", 8, 1), ("A", 1, 1), ("B", 3, 1)) == ["A", "B", "C"]


def test_case_b_corroboration_by_a_second_publisher_lifts_a_near_top_event():
    # A: source1 #2 + source2 #5 → 29/30 + 1/10 = 1.0667; B: source1 #1 → 1.
    assert ordering_value(2, 2) > ordering_value(1, 1)
    assert order(("B", 1, 1), ("A", 2, 2)) == ["A", "B"]
    # The bonus is modest: #5 listed by two publishers stays below a single #1 …
    assert order(("A", 5, 2), ("B", 1, 1)) == ["B", "A"]
    # … and #4 + another publisher ties a single #1; the tie goes to more publishers.
    assert ordering_value(4, 2) == ordering_value(1, 1)
    assert order(("B", 1, 1), ("A", 4, 2)) == ["A", "B"]


def test_case_c_weak_multi_source_evidence_does_not_overwhelm_a_strong_native_rank():
    # A: source1 #1; B: source1 #9 + source2 #9 (the best rank counts once, never summed).
    assert order(("B", 9, 2), ("A", 1, 1)) == ["A", "B"]
    # Even three publishers at #9 (bonus capped at two) stay below #1 and #2.
    assert order(("B", 9, 3), ("A", 1, 1), ("C", 2, 1)) == ["A", "C", "B"]
    assert ordering_value(9, 9) == ordering_value(9, 3)


def test_case_d_list_length_is_not_used_to_normalise_ranks():
    # Top 10 list #5 vs Top 30 list #15: the shared scale keeps #5 ahead (26/30 vs 16/30), and a
    # short list's lower rows are not pushed below a long list's rows of the same position: the
    # 4th of a 4-row list equals the 4th of a 30-row list.
    assert order(("thirty-15", 15, 1), ("ten-5", 5, 1)) == ["ten-5", "thirty-15"]
    assert rank_signal(4) == Fraction(27, 30)


def test_case_e_a_stale_event_is_not_eligible_whatever_its_rank():
    assert MAX_REPORT_AGE == timedelta(hours=48)
    observed = [T0]
    assert current_eligible(T0 - timedelta(hours=47, minutes=59), observed)
    assert current_eligible(T0 - timedelta(hours=48), observed)
    assert not current_eligible(T0 - timedelta(hours=48, seconds=1), observed)
    # A report newer than the snapshot (a later report joined the Event) is current.
    assert current_eligible(T0 + timedelta(hours=1), observed)
    # Listed on two lists: age is measured at the more recent observation (the stricter one).
    two = [T0 - timedelta(hours=30), T0]
    assert current_eligible(T0 - timedelta(hours=40), two)
    assert not current_eligible(T0 - timedelta(hours=60), two)
    assert not current_eligible(None, observed)
    assert not current_eligible(T0, [])


def test_case_f_one_publisher_only_needs_no_synthetic_diversity():
    ids = [f"h{rank}" for rank in range(1, 31)]
    shuffled = list(reversed(ids))
    assert order(*[(event, int(event[1:]), 1) for event in shuffled]) == ids


def test_ties_are_deterministic():
    newer, older = T0, T0 - timedelta(hours=5)
    # Same rank on two different lists: newer latest report first, then Event ID.
    assert order(("old", 1, 1, older), ("new", 1, 1, newer)) == ["new", "old"]
    assert order(("b", 2, 1), ("a", 2, 1)) == ["a", "b"]
    # An unknown report time sorts after known ones.
    assert order(("x", 3, 1, None), ("y", 3, 1, older)) == ["y", "x"]
    # Equal values: more publishers first, then the better best rank.
    assert order(("single", 1, 1), ("double", 4, 2)) == ["double", "single"]
    assert order(("deep", 45, 1), ("shallow", 30, 1)) == ["shallow", "deep"]
    assert aggregate_order([]) == []
