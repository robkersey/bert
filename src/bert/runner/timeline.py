"""Unified event timeline.

Every observation Bert makes — whether a host-side ATT request from Bumble
or an over-the-air packet from the sniffer PCAP — is appended here with a
monotonic-nanosecond timestamp and a stable id. Test-case assertions
correlate events across both sources via this single log.
"""

from __future__ import annotations

import bisect
import itertools
import time
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import Any, Iterable


class EventSource(str, Enum):
    HOST = "host"  # observed by Bumble
    OTA = "ota"  # observed by the sniffer / PCAP


@dataclass(frozen=True, slots=True)
class TimelineEvent:
    id: str
    t_ns: int
    source: EventSource
    kind: str  # e.g. "att.notify", "btle.adv_ind", "gatt.connection_complete"
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def t_s(self) -> float:
        return self.t_ns / 1_000_000_000


class Timeline:
    """Append-only, thread-safe, sortable event log."""

    def __init__(self) -> None:
        self._events: list[TimelineEvent] = []
        self._counter = itertools.count(1)
        self._lock = Lock()
        self._wall_origin_ns: int | None = None

    # ------------------------------------------------------------------ #

    def now_ns(self) -> int:
        """Monotonic ns timestamp anchored to the timeline's wall origin."""
        if self._wall_origin_ns is None:
            self._wall_origin_ns = time.monotonic_ns()
        return time.monotonic_ns()

    def add(
        self,
        kind: str,
        source: EventSource = EventSource.HOST,
        *,
        data: dict[str, Any] | None = None,
        t_ns: int | None = None,
    ) -> TimelineEvent:
        eid = f"{source.value[0]}{next(self._counter):06d}"  # h000001 / o000001
        ev = TimelineEvent(
            id=eid,
            t_ns=t_ns if t_ns is not None else self.now_ns(),
            source=source,
            kind=kind,
            data=dict(data or {}),
        )
        with self._lock:
            # Keep the list sorted-on-insert; PCAP events arrive out of order
            # relative to host events because they're folded in post-run.
            keys = [e.t_ns for e in self._events]
            idx = bisect.bisect_right(keys, ev.t_ns)
            self._events.insert(idx, ev)
        return ev

    # ------------------------------------------------------------------ #

    def __len__(self) -> int:
        return len(self._events)

    def __iter__(self) -> Iterable[TimelineEvent]:
        return iter(list(self._events))

    def select(
        self,
        *,
        kind: str | None = None,
        source: EventSource | None = None,
        since_ns: int | None = None,
        until_ns: int | None = None,
    ) -> list[TimelineEvent]:
        out = []
        for e in self._events:
            if kind is not None and e.kind != kind:
                continue
            if source is not None and e.source != source:
                continue
            if since_ns is not None and e.t_ns < since_ns:
                continue
            if until_ns is not None and e.t_ns > until_ns:
                continue
            out.append(e)
        return out

    def deltas_ms(self, events: list[TimelineEvent]) -> list[float]:
        """Inter-arrival gaps in milliseconds."""
        if len(events) < 2:
            return []
        return [(b.t_ns - a.t_ns) / 1_000_000 for a, b in zip(events, events[1:])]
