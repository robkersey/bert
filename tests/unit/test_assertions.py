"""Assertion helpers behave deterministically against synthetic timelines."""

from __future__ import annotations

import pytest

from bert.runner.assertions import (
    AssertionFailure,
    assert_before,
    assert_cadence_within,
    assert_in_range,
)
from bert.runner.timeline import EventSource, Timeline


def test_assert_in_range_passes_within_slack() -> None:
    assert_in_range(11.0, label="x", lo=10.0, hi=10.0, slack=0.10)


def test_assert_in_range_fails_outside_slack() -> None:
    with pytest.raises(AssertionFailure):
        assert_in_range(20.0, label="x", lo=10.0, hi=10.0, slack=0.10)


def test_cadence_within_bounds() -> None:
    tl = Timeline()
    for i in range(10):
        tl.add("att.notify", EventSource.HOST, t_ns=i * 1_000_000_000)  # 1s gap
    assert_cadence_within(
        tl, kind="att.notify", lo_ms=900, hi_ms=1100, slack=0.05, min_samples=5
    )


def test_cadence_outside_bounds_raises() -> None:
    tl = Timeline()
    for i in range(10):
        tl.add("att.notify", EventSource.HOST, t_ns=i * 100_000_000)  # 100ms — too fast
    with pytest.raises(AssertionFailure):
        assert_cadence_within(
            tl, kind="att.notify", lo_ms=250, hi_ms=2000, slack=0.10, min_samples=5
        )


def test_cadence_too_few_samples_raises() -> None:
    tl = Timeline()
    tl.add("att.notify", EventSource.HOST, t_ns=0)
    tl.add("att.notify", EventSource.HOST, t_ns=1_000_000_000)
    with pytest.raises(AssertionFailure):
        assert_cadence_within(
            tl, kind="att.notify", lo_ms=900, hi_ms=1100, min_samples=5
        )


def test_assert_before_fails_when_reordered() -> None:
    tl = Timeline()
    later = tl.add("a", t_ns=10)
    earlier = tl.add("b", t_ns=20)
    with pytest.raises(AssertionFailure):
        assert_before(earlier, later, label="ordering")
