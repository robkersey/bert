"""Per-test execution context handed to each procedure."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from bert.runner.timeline import Timeline

if TYPE_CHECKING:
    from bert.adapters.bumble_host import BumbleHost
    from bert.ir.schema import Profile, TestCase


@dataclass
class TestContext:
    """Mutable state for one test case's execution.

    Procedures call ``ctx.bumble.*`` to drive the DUT, ``ctx.timeline.add(...)``
    to record observations, and the ``assert_*`` helpers from
    :mod:`bert.runner.assertions` to make claims.
    """

    __test__ = False  # not a pytest collection target

    profile: "Profile"
    test_case: "TestCase"
    bumble: "BumbleHost"
    timeline: Timeline
    started_ns: int = 0
    ended_ns: int = 0
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def window(self) -> tuple[int, int]:
        return (self.started_ns, self.ended_ns or self.timeline.now_ns())
