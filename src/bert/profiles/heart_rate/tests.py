"""Heart Rate Profile-specific test procedures.

Registered via the ``bert.procedures`` entry point. The plain
``discover_mandatory_services`` procedure used by ``TC_HRP_001`` is the
generic one from :mod:`bert.runner.procedures`; the rest live here.
"""

from __future__ import annotations

import asyncio
import logging
import statistics
from struct import unpack_from

from bert.runner import (
    AssertionFailure,
    EventSource,
    TestContext,
    assert_cadence_within,
    assert_in_range,
    testcase,
)
from bert.runner.assertions import SkipTest

log = logging.getLogger(__name__)

HRS_UUID = "0x180D"
HRM_UUID = "0x2A37"
HRCP_UUID = "0x2A39"


def register() -> None:
    """Importing this module registers the @testcase functions below; this
    hook exists only so the entry-point loader has something callable."""


@testcase("subscribe_and_measure_cadence")
async def subscribe_and_measure_cadence(ctx: TestContext) -> None:
    """TC_HRP_002 — subscribe to HRM notifications, verify cadence bound."""

    bound = ctx.profile.procedure(ctx.test_case.bound) if ctx.test_case.bound else None
    if bound is None or not bound.bounds or "interval_ms" not in bound.bounds:
        raise AssertionFailure(
            "TC_HRP_002: profile is missing the hrm_notify_cadence procedure bounds"
        )
    interval = bound.bounds["interval_ms"]

    duration = max(5.0, min(ctx.test_case.timeout_s - 2, 15.0))
    notifications = await ctx.bumble.subscribe_and_collect(
        HRS_UUID, HRM_UUID, duration_s=duration
    )
    if len(notifications) < 5:
        raise AssertionFailure(
            f"TC_HRP_002: only received {len(notifications)} HRM notifications "
            f"in {duration:.1f}s — expected ≥5",
        )

    # Sanity-decode the first frame to make sure flags + HR value parse.
    flags, hr = _decode_hrm(notifications[0])
    log.info("first HRM frame: flags=0x%02x hr=%d", flags, hr)

    assert_cadence_within(
        ctx.timeline,
        kind="att.notify",
        lo_ms=interval.min,
        hi_ms=interval.max,
        source=EventSource.HOST,
        since_ns=ctx.started_ns,
        label="HRM cadence",
    )


@testcase("hrcp_reset_behaviour")
async def hrcp_reset_behaviour(ctx: TestContext) -> None:
    """TC_HRP_004 — write 0x01 to HRCP, expect Energy Expended to zero in next HRM.

    Skips cleanly if the DUT doesn't expose the (optional) Heart Rate Control
    Point characteristic. The HRP profile lists 0x2A39 as conditional on the
    DUT supporting Energy Expended, so absence is a legitimate configuration.
    """

    if not await ctx.bumble.has_characteristic(HRS_UUID, HRCP_UUID):
        raise SkipTest(
            f"DUT does not expose Heart Rate Control Point ({HRCP_UUID}); "
            f"this is allowed by the HRP spec when Energy Expended is unsupported."
        )

    pre = await ctx.bumble.subscribe_and_collect(HRS_UUID, HRM_UUID, duration_s=4.0)
    pre_ee = _energy_expended_or_none(pre[-1]) if pre else None

    await ctx.bumble.write(HRS_UUID, HRCP_UUID, b"\x01", with_response=True)
    # Allow the DUT a moment to apply the reset.
    await asyncio.sleep(0.5)

    post = await ctx.bumble.subscribe_and_collect(HRS_UUID, HRM_UUID, duration_s=4.0)
    post_ee = _energy_expended_or_none(post[-1]) if post else None

    if post_ee is None:
        raise AssertionFailure(
            "TC_HRP_004: no Energy Expended field in post-reset HRM frames; "
            "either the DUT does not support EE (test should not have been "
            "selected) or it stopped notifying after the write."
        )
    if post_ee != 0:
        raise AssertionFailure(
            f"TC_HRP_004: Energy Expended after reset was {post_ee} (pre={pre_ee}); "
            f"expected 0",
            detail={"pre_ee": pre_ee, "post_ee": post_ee},
        )


@testcase("ota_advertising_interval")
async def ota_advertising_interval(ctx: TestContext) -> None:
    """TC_HRP_003 — over-the-air advertising-interval check from the PCAP fold.

    The sniffer is captured for the whole run; this procedure runs at test-case
    dispatch time but is meaningful only after teardown when ``fold_pcap_into_timeline``
    has populated the timeline with ``btle.adv`` events. v1 takes the pragmatic
    approach: collect a short capture window during the test (the sniffer is
    already running), then assert. If the DUT only advertises pre-connection
    we'll already have those packets in the OTA stream by the time we run.
    """

    bound = ctx.profile.procedure(ctx.test_case.bound) if ctx.test_case.bound else None
    if bound is None or not bound.bounds or "interval_ms" not in bound.bounds:
        raise AssertionFailure("TC_HRP_003: missing advertising_interval bounds")
    interval = bound.bounds["interval_ms"]

    # Wait briefly so any pre-connection adv frames already in the sniffer
    # buffer have a chance to flush into the PCAP. The runner-level fold runs
    # at teardown, but for live mode we look at host-observed scan timestamps
    # in lieu of OTA. Either source is acceptable for this assertion.
    await asyncio.sleep(2.0)

    # Wait briefly so the post-test PCAP fold has run and adv events landed.
    # The runner folds the PCAP at teardown, but this procedure fires before
    # teardown — so we look at host events first, then any OTA events that
    # landed via an earlier fold (PCAP analyse re-runs).
    ota_all = ctx.timeline.select(kind="btle.adv", source=EventSource.OTA)
    # Filter to advertisements from the DUT only — scope to our connection's
    # peer address if we have it.
    dut_addr = (ctx.extras.get("dut_address") or "").upper().replace(":", "")
    if dut_addr:
        ota = [
            e for e in ota_all
            if (e.data.get("adva") or "").upper().replace(":", "").endswith(dut_addr[-12:])
        ]
    else:
        ota = ota_all
    if len(ota) >= 5:
        deltas = ctx.timeline.deltas_ms(ota)
        median = statistics.median(deltas)
        assert_in_range(
            median,
            label="advertising interval (OTA, median)",
            lo=interval.min,
            hi=interval.max,
            slack=0.10,
            detail={"samples": len(ota), "median_ms": median, "source": "ota"},
        )
        return

    # Fallback: host-side scan-result timestamps. These ARE less precise than
    # the sniffer's hardware-stamped OTA times, but they're better than nothing
    # and adequate for catching gross misconfiguration (e.g. a DUT advertising
    # at 30s intervals instead of 30ms). Apply extra slack to acknowledge the
    # imprecision and tag the report so the user knows.
    host_adv = ctx.timeline.select(kind="host.scan_match")
    if len(host_adv) < 5:
        # We may have only seen the single match that triggered the connection;
        # disconnect happens fast enough that further scan results don't land.
        raise SkipTest(
            f"TC_HRP_003: only {len(host_adv)} advertising sample(s) captured "
            f"from host; need ≥5 for a cadence estimate. Sniffer PCAP fold "
            f"returned 0 packets too — likely the Nordic-DLT decoder needs "
            f"work. Tracked separately."
        )
    deltas = ctx.timeline.deltas_ms(host_adv)
    median = statistics.median(deltas)
    assert_in_range(
        median,
        label="advertising interval (HOST scan, median)",
        lo=interval.min,
        hi=interval.max,
        slack=0.50,  # generous — host timestamps are coarse
        detail={
            "samples": len(host_adv),
            "median_ms": median,
            "source": "host (sniffer fold returned 0 packets)",
        },
    )


# --------------------------------------------------------------------------- #
# HRM frame decoding — minimal, only what the procedures above need           #
# --------------------------------------------------------------------------- #


def _decode_hrm(frame: bytes) -> tuple[int, int]:
    """Return ``(flags, hr_value)`` from a Heart Rate Measurement notification."""
    if not frame:
        raise AssertionFailure("empty HRM notification")
    flags = frame[0]
    if flags & 0x01:  # 16-bit HR value
        if len(frame) < 3:
            raise AssertionFailure("HRM frame truncated (16-bit HR claimed)")
        return flags, unpack_from("<H", frame, 1)[0]
    if len(frame) < 2:
        raise AssertionFailure("HRM frame truncated (8-bit HR claimed)")
    return flags, frame[1]


def _energy_expended_or_none(frame: bytes) -> int | None:
    """Extract the Energy Expended field if present in this HRM frame."""
    if not frame:
        return None
    flags = frame[0]
    if not (flags & 0x08):
        return None  # EE not supported in this frame
    offset = 1 + (2 if flags & 0x01 else 1)  # past flags + HR value
    # Skip 4 bytes of sensor-contact-status / RR fields that may precede EE? No:
    # per spec the order is Flags, HR, [Energy Expended (uint16)], [RR Intervals...].
    if len(frame) < offset + 2:
        return None
    return unpack_from("<H", frame, offset)[0]
