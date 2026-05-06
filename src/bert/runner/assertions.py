"""Assertion helpers used by procedures.

A failing assertion raises :class:`AssertionFailure`, which the runner catches
and turns into a structured failure record (preserving the timeline window so
the report can show host log + relevant packets).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from bert.runner.timeline import EventSource, Timeline, TimelineEvent


@dataclass
class AssertionFailure(AssertionError):
    """A failed assertion. Carries cross-references for the report."""

    message: str
    host_event_ids: list[str] = field(default_factory=list)
    ota_event_ids: list[str] = field(default_factory=list)
    timeline_window: tuple[int, int] | None = None
    detail: dict[str, object] = field(default_factory=dict)

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.message


class SkipTest(Exception):
    """Raise from a procedure to skip the current test case with a reason.

    Use when an optional/conditional precondition isn't satisfied on the DUT
    (e.g. an HRP DUT that doesn't expose the optional Heart Rate Control Point).
    The runner records the case as ``skipped`` rather than ``failed``.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# --------------------------------------------------------------------------- #
# Generic helpers                                                              #
# --------------------------------------------------------------------------- #


def assert_present(value: object, *, label: str) -> None:
    if value is None:
        raise AssertionFailure(f"expected {label} to be present, got None")


def assert_eq(actual: object, expected: object, *, label: str) -> None:
    if actual != expected:
        raise AssertionFailure(
            f"{label}: expected {expected!r}, got {actual!r}",
            detail={"actual": repr(actual), "expected": repr(expected)},
        )


def assert_in_range(
    value: float,
    *,
    label: str,
    lo: float,
    hi: float,
    slack: float = 0.0,
    detail: dict[str, object] | None = None,
) -> None:
    """Inclusive range check with optional fractional slack (e.g. 0.10 → 10%)."""

    lo_eff = lo * (1.0 - slack)
    hi_eff = hi * (1.0 + slack)
    if not (lo_eff <= value <= hi_eff):
        raise AssertionFailure(
            f"{label}: {value:.3f} not in [{lo:.3f}, {hi:.3f}] "
            f"(slack {slack * 100:.0f}% → effective [{lo_eff:.3f}, {hi_eff:.3f}])",
            detail={"value": value, "lo": lo, "hi": hi, "slack": slack, **(detail or {})},
        )


# --------------------------------------------------------------------------- #
# Cadence / interval                                                           #
# --------------------------------------------------------------------------- #


def assert_cadence_within(
    timeline: Timeline,
    *,
    kind: str,
    lo_ms: float,
    hi_ms: float,
    source: EventSource | None = None,
    since_ns: int | None = None,
    until_ns: int | None = None,
    slack: float = 0.10,
    min_samples: int = 5,
    label: str | None = None,
) -> None:
    """Assert median inter-arrival gap of ``kind`` events lies in [lo_ms, hi_ms]."""

    events = timeline.select(kind=kind, source=source, since_ns=since_ns, until_ns=until_ns)
    if len(events) < min_samples:
        raise AssertionFailure(
            f"cadence({kind}): need ≥{min_samples} samples, got {len(events)}",
            host_event_ids=[e.id for e in events if e.source == EventSource.HOST],
            ota_event_ids=[e.id for e in events if e.source == EventSource.OTA],
            timeline_window=(since_ns or 0, until_ns or 0),
        )
    deltas = timeline.deltas_ms(events)
    median = statistics.median(deltas)
    p95 = _percentile(deltas, 0.95)
    eff_lo = lo_ms * (1.0 - slack)
    eff_hi = hi_ms * (1.0 + slack)
    if not (eff_lo <= median <= eff_hi) or p95 > eff_hi * 1.5:
        raise AssertionFailure(
            f"{label or 'cadence(' + kind + ')'}: "
            f"median={median:.1f}ms p95={p95:.1f}ms outside "
            f"[{lo_ms:.0f}, {hi_ms:.0f}]ms (slack {slack * 100:.0f}%)",
            host_event_ids=[e.id for e in events if e.source == EventSource.HOST],
            ota_event_ids=[e.id for e in events if e.source == EventSource.OTA],
            timeline_window=(events[0].t_ns, events[-1].t_ns),
            detail={
                "samples": len(events),
                "median_ms": median,
                "p95_ms": p95,
                "min_ms": min(deltas),
                "max_ms": max(deltas),
            },
        )


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


# --------------------------------------------------------------------------- #
# Timeline ordering                                                            #
# --------------------------------------------------------------------------- #


def assert_before(
    earlier: TimelineEvent | None,
    later: TimelineEvent | None,
    *,
    label: str,
) -> None:
    if earlier is None or later is None:
        raise AssertionFailure(
            f"{label}: missing event(s) "
            f"(earlier={'ok' if earlier else 'MISSING'}, later={'ok' if later else 'MISSING'})"
        )
    if earlier.t_ns >= later.t_ns:
        raise AssertionFailure(
            f"{label}: expected {earlier.kind} before {later.kind}, but "
            f"Δ={(earlier.t_ns - later.t_ns) / 1_000_000:.3f}ms",
            host_event_ids=[
                e.id for e in (earlier, later) if e.source == EventSource.HOST
            ],
            ota_event_ids=[
                e.id for e in (earlier, later) if e.source == EventSource.OTA
            ],
            timeline_window=(min(earlier.t_ns, later.t_ns), max(earlier.t_ns, later.t_ns)),
        )
