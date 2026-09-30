"""Product-1 bounded AI: the Event cap and domain balancing are enforced in code."""

import argparse
import asyncio

import pytest

from industry_intelligence.cli import _ai_event_cap
from industry_intelligence.services import intelligence_update as update
from industry_intelligence.services.ai_enrichment import (
    AI_MAX_EVENTS,
    DEFAULT_MAX_EVENTS,
    round_robin,
    validate_max_events,
)


def test_default_and_hard_cap():
    assert (DEFAULT_MAX_EVENTS, AI_MAX_EVENTS) == (12, 15)
    assert update.DEFAULT_AI_LIMIT == DEFAULT_MAX_EVENTS
    assert [validate_max_events(value) for value in (1, 12, 15)] == [1, 12, 15]


@pytest.mark.parametrize("value", [0, -1, 16, 335, None, "12", 12.0, True])
def test_out_of_range_or_non_integer_caps_are_refused(value):
    with pytest.raises(ValueError):
        validate_max_events(value)


@pytest.mark.parametrize("value", ["0", "16", "all", "unlimited", "335", ""])
def test_cli_refuses_unbounded_ai_limits(value):
    with pytest.raises(argparse.ArgumentTypeError):
        _ai_event_cap(value)
    assert _ai_event_cap("12") == 12


def test_round_robin_balances_domains_and_skips_exhausted_ones():
    computer, finance, gaming = ["c1", "c2", "c3"], ["f1"], ["g1", "g2"]
    assert round_robin([computer, finance, gaming], 12) == ["c1", "f1", "g1", "c2", "g2", "c3"]
    assert round_robin([computer, finance, gaming], 4) == ["c1", "f1", "g1", "c2"]
    assert round_robin([[], [], []], 5) == []
    # Pure: inputs are untouched, and the result is the same every time.
    assert computer == ["c1", "c2", "c3"]
    assert round_robin([computer, finance, gaming], 4) == ["c1", "f1", "g1", "c2"]


def test_run_paths_refuse_an_invalid_cap_before_any_lock_or_stage(settings):
    class Lock:
        def acquire(self):
            raise AssertionError("the lock must not be taken")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("no stage may run")

    for coroutine in (
        update.execute_update(settings, forbidden, ai_limit=16, lock=Lock()),
        update.run_update(settings, forbidden, ai_limit=0, check=forbidden),
        update.plan_update(settings, forbidden, ai_limit=100, check=forbidden),
    ):
        with pytest.raises(ValueError):
            asyncio.run(coroutine)
