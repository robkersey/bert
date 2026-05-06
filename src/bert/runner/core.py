"""Top-level orchestrator.

Owns the run lifecycle: bring up sniffer + Bumble host, scan, connect,
dispatch test cases, tear down, fold the PCAP into the timeline, and emit
the report.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from bert.adapters.bumble_host import BumbleHost
from bert.adapters.pcap import fold_pcap_into_timeline
from bert.adapters.sniffer import SnifferCapture
from bert.ir import Profile, TestCase, TestCaseSource, assert_runnable
from bert.runner import registry
from bert.runner.assertions import AssertionFailure, SkipTest
from bert.runner.context import TestContext
from bert.runner.timeline import Timeline

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Result types                                                                 #
# --------------------------------------------------------------------------- #


@dataclass
class TestResult:
    __test__ = False  # not a pytest collection target

    test_case: TestCase
    status: str  # "passed" | "failed" | "skipped" | "error"
    started_ns: int
    ended_ns: int
    failure: AssertionFailure | None = None
    error: str | None = None
    skip_reason: str | None = None

    @property
    def duration_s(self) -> float:
        return (self.ended_ns - self.started_ns) / 1_000_000_000


@dataclass
class RunResult:
    profile: Profile
    started_at: datetime
    ended_at: datetime | None = None
    results: list[TestResult] = field(default_factory=list)
    pcap_path: Path | None = None
    timeline: Timeline | None = None

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.status == "passed")

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.status in {"failed", "error"})

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.status == "skipped")

    @property
    def overall(self) -> str:
        if self.failed:
            return "failed"
        if self.passed == 0:
            return "no-tests"
        return "passed"


# --------------------------------------------------------------------------- #
# Configuration                                                                #
# --------------------------------------------------------------------------- #


@dataclass
class RunConfig:
    profile: Profile
    dut_address: str | None = None
    dut_name: str | None = None
    passkey: int | None = None
    report_dir: Path = field(default_factory=lambda: Path("runs"))
    repeats: int = 1
    quorum: int = 1
    sniffer_enabled: bool = True
    hci_transport: str | None = None  # e.g. "serial:/dev/cu.usbmodem...,1000000"
    allow_draft: bool = False


# --------------------------------------------------------------------------- #
# Runner                                                                       #
# --------------------------------------------------------------------------- #


class Runner:
    def __init__(self, config: RunConfig) -> None:
        self.config = config
        self.timeline = Timeline()
        self._dut_address: str | None = None

    async def run(self) -> RunResult:
        cfg = self.config
        assert_runnable(cfg.profile, allow_draft=cfg.allow_draft)

        run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        run_dir = cfg.report_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        pcap_path = run_dir / "capture.pcapng" if cfg.sniffer_enabled else None

        result = RunResult(
            profile=cfg.profile,
            started_at=datetime.now(UTC),
            pcap_path=pcap_path,
            timeline=self.timeline,
        )

        sniffer_ctx = (
            SnifferCapture(output_path=pcap_path) if cfg.sniffer_enabled and pcap_path else None
        )

        # Two-phase test run:
        #   Phase 1: Bumble connected. Run host-source and ``both`` test cases.
        #   Phase 2: Bumble disconnected, sniffer stopped, PCAP folded.
        #            Run ota-source test cases against the populated timeline.
        # Order is preserved within each phase so the report rows match the
        # IR's declared order.

        host_phase = [
            tc for tc in cfg.profile.test_cases
            if tc.source in (TestCaseSource.HOST, TestCaseSource.BOTH)
        ]
        ota_phase = [
            tc for tc in cfg.profile.test_cases
            if tc.source == TestCaseSource.OTA
        ]
        results_by_id: dict[str, TestResult] = {}

        try:
            if sniffer_ctx is not None:
                await sniffer_ctx.start()

            async with BumbleHost.open(
                transport=cfg.hci_transport,
                timeline=self.timeline,
                passkey=cfg.passkey,
            ) as host:
                match = await host.scan_and_connect(
                    dut_address=cfg.dut_address,
                    dut_name=cfg.dut_name,
                )
                self._dut_address = match.address
                for tc in host_phase:
                    res = await self._run_one(tc, host)
                    results_by_id[tc.id] = res
        finally:
            if sniffer_ctx is not None:
                await sniffer_ctx.stop()

        if pcap_path is not None and pcap_path.exists():
            fold_pcap_into_timeline(pcap_path, self.timeline)

        # Phase 2: OTA-source test cases. They don't need the Bumble host
        # but we still pass one (it'll be None) for context-shape consistency.
        for tc in ota_phase:
            res = await self._run_one(tc, host=None)  # type: ignore[arg-type]
            results_by_id[tc.id] = res

        # Re-emit results in the IR's original order so the report is stable.
        result.results = [results_by_id[tc.id] for tc in cfg.profile.test_cases if tc.id in results_by_id]

        result.ended_at = datetime.now(UTC)
        return result

    async def _run_one(self, tc: TestCase, host: BumbleHost) -> TestResult:
        if tc.applies_if and not _evaluate_applies_if(tc.applies_if, self.config.profile):
            log.info("skipping %s: applies_if=%r evaluated false", tc.id, tc.applies_if)
            now = self.timeline.now_ns()
            return TestResult(
                test_case=tc,
                status="skipped",
                started_ns=now,
                ended_ns=now,
                skip_reason=f"applies_if: {tc.applies_if}",
            )

        ctx = TestContext(
            profile=self.config.profile,
            test_case=tc,
            bumble=host,
            timeline=self.timeline,
            started_ns=self.timeline.now_ns(),
            extras={"dut_address": self._dut_address} if self._dut_address else {},
        )
        try:
            proc = registry.get(tc.procedure)
        except KeyError as exc:
            ctx.ended_ns = self.timeline.now_ns()
            return TestResult(
                test_case=tc,
                status="error",
                started_ns=ctx.started_ns,
                ended_ns=ctx.ended_ns,
                error=str(exc),
            )

        try:
            await asyncio.wait_for(proc(ctx), timeout=tc.timeout_s)
            ctx.ended_ns = self.timeline.now_ns()
            return TestResult(
                test_case=tc,
                status="passed",
                started_ns=ctx.started_ns,
                ended_ns=ctx.ended_ns,
            )
        except SkipTest as skip:
            ctx.ended_ns = self.timeline.now_ns()
            return TestResult(
                test_case=tc,
                status="skipped",
                started_ns=ctx.started_ns,
                ended_ns=ctx.ended_ns,
                skip_reason=skip.reason,
            )
        except AssertionFailure as fail:
            ctx.ended_ns = self.timeline.now_ns()
            if fail.timeline_window is None:
                fail.timeline_window = (ctx.started_ns, ctx.ended_ns)
            return TestResult(
                test_case=tc,
                status="failed",
                started_ns=ctx.started_ns,
                ended_ns=ctx.ended_ns,
                failure=fail,
            )
        except asyncio.TimeoutError:
            ctx.ended_ns = self.timeline.now_ns()
            return TestResult(
                test_case=tc,
                status="failed",
                started_ns=ctx.started_ns,
                ended_ns=ctx.ended_ns,
                failure=AssertionFailure(
                    f"timed out after {tc.timeout_s}s",
                    timeline_window=(ctx.started_ns, ctx.ended_ns),
                ),
            )
        except Exception as exc:  # noqa: BLE001 - test errors must not crash the runner
            log.exception("error running %s", tc.id)
            ctx.ended_ns = self.timeline.now_ns()
            return TestResult(
                test_case=tc,
                status="error",
                started_ns=ctx.started_ns,
                ended_ns=ctx.ended_ns,
                error=f"{type(exc).__name__}: {exc}",
            )


def _evaluate_applies_if(expr: str, profile: Profile) -> bool:
    """Tiny gate-expression evaluator used by ``test_case.applies_if``.

    Supports only the dialect used by shipped profiles today:
    ``"<UUID> present"`` — true if a service or characteristic with that UUID
    appears in the profile.
    """

    expr = expr.strip()
    if expr.endswith(" present"):
        target = expr[: -len(" present")].strip().upper()
        for svc in profile.services:
            if svc.uuid.upper() == target:
                return True
            for char in svc.characteristics:
                if char.uuid.upper() == target:
                    return True
        return False
    # Unknown dialect → run by default; the profile author can tighten later.
    return True
