"""Timeline insertion ordering and queries."""

from __future__ import annotations

from bert.runner.timeline import EventSource, Timeline


def test_inserts_in_time_order() -> None:
    tl = Timeline()
    tl.add("a", EventSource.HOST, t_ns=300)
    tl.add("b", EventSource.OTA, t_ns=100)
    tl.add("c", EventSource.HOST, t_ns=200)
    kinds = [e.kind for e in tl]
    assert kinds == ["b", "c", "a"]


def test_select_filters_by_kind_and_source() -> None:
    tl = Timeline()
    tl.add("att.notify", EventSource.HOST, t_ns=10)
    tl.add("att.notify", EventSource.OTA, t_ns=20)
    tl.add("btle.adv", EventSource.OTA, t_ns=30)
    host_notifs = tl.select(kind="att.notify", source=EventSource.HOST)
    assert len(host_notifs) == 1
    assert host_notifs[0].t_ns == 10


def test_deltas_ms() -> None:
    tl = Timeline()
    e1 = tl.add("x", t_ns=0)
    e2 = tl.add("x", t_ns=1_000_000)
    e3 = tl.add("x", t_ns=3_000_000)
    deltas = tl.deltas_ms([e1, e2, e3])
    assert deltas == [1.0, 2.0]
