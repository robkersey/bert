"""Render a RunResult as JUnit XML for CI consumption."""

from __future__ import annotations

from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring

from bert.runner.core import RunResult


def render(result: RunResult, *, path: Path | None = None) -> str:
    suite = Element(
        "testsuite",
        attrib={
            "name": f"bert.{result.profile.metadata.abbrev}",
            "tests": str(len(result.results)),
            "failures": str(sum(1 for r in result.results if r.status == "failed")),
            "errors": str(sum(1 for r in result.results if r.status == "error")),
            "skipped": str(result.skipped),
            "timestamp": result.started_at.isoformat(),
        },
    )
    for r in result.results:
        case = SubElement(
            suite,
            "testcase",
            attrib={
                "classname": result.profile.metadata.abbrev,
                "name": f"{r.test_case.id}: {r.test_case.title}",
                "time": f"{r.duration_s:.3f}",
            },
        )
        if r.status == "failed" and r.failure is not None:
            failure = SubElement(case, "failure", attrib={"message": r.failure.message})
            failure.text = _failure_detail(r)
        elif r.status == "error":
            err = SubElement(case, "error", attrib={"message": r.error or "error"})
            err.text = r.error or ""
        elif r.status == "skipped":
            SubElement(case, "skipped", attrib={"message": r.skip_reason or ""})

    suites = Element("testsuites")
    suites.append(suite)
    xml = b'<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(suites)
    if path is not None:
        path.write_bytes(xml)
    return xml.decode("utf-8")


def _failure_detail(r: object) -> str:
    fail = getattr(r, "failure", None)
    if fail is None:
        return ""
    parts = [fail.message]
    if fail.detail:
        parts.append("detail: " + repr(fail.detail))
    if fail.host_event_ids:
        parts.append("host events: " + ", ".join(fail.host_event_ids))
    if fail.ota_event_ids:
        parts.append("ota events: " + ", ".join(fail.ota_event_ids))
    return "\n".join(parts)
