"""Generic test-case procedures, shared across profiles.

Importing this package registers procedures via the ``@testcase`` decorator.
The runner imports it eagerly through :mod:`bert.runner.registry`.
"""

from __future__ import annotations

from bert.runner.assertions import (
    AssertionFailure,
    assert_cadence_within,
    assert_present,
)
from bert.runner.context import TestContext
from bert.runner.registry import testcase


def register() -> None:
    """No-op kept for symmetry with profile-suite ``register()`` hooks."""


@testcase("discover_mandatory_services")
async def discover_mandatory_services(ctx: TestContext) -> None:
    """Verify every mandatory service in the profile is present on the DUT."""

    services = await ctx.bumble.discover_services()
    found = {s.upper() for s in services}
    missing: list[str] = []
    for svc in ctx.profile.services:
        if svc.requirement.value != "mandatory":
            continue
        if svc.uuid.upper() not in found:
            missing.append(f"{svc.uuid} ({svc.name or 'unnamed'})")
    if missing:
        raise AssertionFailure(
            "DUT missing mandatory services: " + ", ".join(missing),
            detail={"discovered": sorted(found), "missing": missing},
        )


@testcase("read_characteristic")
async def read_characteristic(ctx: TestContext) -> None:
    """Generic: read a characteristic identified via ``ctx.test_case.requires``.

    The first ``requires`` entry is interpreted as ``services.<S>.characteristics.<C>``.
    """

    if not ctx.test_case.requires:
        raise AssertionFailure("read_characteristic needs a `requires:` entry")
    target = ctx.test_case.requires[0]
    parts = target.split(".")
    if len(parts) < 4:
        raise AssertionFailure(f"unparseable requires path: {target!r}")
    svc_uuid, char_uuid = parts[1], parts[3]
    value = await ctx.bumble.read(svc_uuid, char_uuid)
    assert_present(value, label=f"read({svc_uuid}/{char_uuid})")


@testcase("subscribe_and_count")
async def subscribe_and_count(ctx: TestContext) -> None:
    """Subscribe to a notify characteristic and require ≥N notifications.

    ``ctx.extras['min_count']`` controls N (default 5).
    """

    target = ctx.test_case.requires[0]
    parts = target.split(".")
    svc_uuid, char_uuid = parts[1], parts[3]
    received = await ctx.bumble.subscribe_and_collect(
        svc_uuid, char_uuid, duration_s=min(ctx.test_case.timeout_s - 1, 10)
    )
    if len(received) < ctx.extras.get("min_count", 5):
        raise AssertionFailure(
            f"expected ≥{ctx.extras.get('min_count', 5)} notifications from "
            f"{svc_uuid}/{char_uuid}, got {len(received)}"
        )
    bound = ctx.profile.procedure(ctx.test_case.bound) if ctx.test_case.bound else None
    if bound and bound.bounds and "interval_ms" in bound.bounds:
        b = bound.bounds["interval_ms"]
        assert_cadence_within(
            ctx.timeline,
            kind="att.notify",
            lo_ms=b.min,
            hi_ms=b.max,
            since_ns=ctx.started_ns,
            label=f"{ctx.test_case.id} cadence",
        )
