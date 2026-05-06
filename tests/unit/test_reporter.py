"""Reporter golden-output smoke tests."""

from __future__ import annotations

from datetime import UTC, datetime
from xml.etree.ElementTree import fromstring

from bert.ir import load_packaged
from bert.reporter import junit, markdown
from bert.runner.assertions import AssertionFailure
from bert.runner.core import RunResult, TestResult
from bert.runner.timeline import Timeline


def _result_with_one_failure() -> RunResult:
    p = load_packaged("heart_rate")
    started = datetime.now(UTC)
    tl = Timeline()
    tc = p.test_cases[1]  # TC_HRP_002
    fail = AssertionFailure(
        "median 50ms outside [250, 2000]",
        host_event_ids=["h000001"],
        ota_event_ids=["o000001"],
        timeline_window=(0, 1_000_000_000),
    )
    return RunResult(
        profile=p,
        started_at=started,
        ended_at=started,
        results=[
            TestResult(test_case=p.test_cases[0], status="passed", started_ns=0, ended_ns=1),
            TestResult(test_case=tc, status="failed", started_ns=1, ended_ns=2, failure=fail),
        ],
        timeline=tl,
    )


def test_junit_xml_well_formed() -> None:
    r = _result_with_one_failure()
    xml = junit.render(r)
    root = fromstring(xml)
    suite = root.find("testsuite")
    assert suite is not None
    assert suite.get("tests") == "2"
    assert suite.get("failures") == "1"
    failure = suite.findall("testcase")[1].find("failure")
    assert failure is not None
    assert "median" in (failure.get("message") or "")


def test_markdown_contains_failure_section() -> None:
    text = markdown.render(_result_with_one_failure())
    assert "TC_HRP_002" in text
    assert "## Failures" in text
    assert "median 50ms" in text
