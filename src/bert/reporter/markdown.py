"""Markdown report — easy to paste into a PR or chat."""

from __future__ import annotations

from pathlib import Path

from bert.runner.core import RunResult


def render(result: RunResult, *, path: Path | None = None) -> str:
    md = result.profile.metadata
    lines = [
        f"# {md.name} {md.version} — compliance report",
        "",
        f"- **Result:** `{result.overall.upper()}`",
        f"- **Profile:** {md.abbrev} {md.version}",
        f"- **Started:** {result.started_at.isoformat()}",
        f"- **Duration:** "
        + (
            f"{(result.ended_at - result.started_at).total_seconds():.1f}s"
            if result.ended_at
            else "n/a"
        ),
        f"- **Tests:** {len(result.results)} ({result.passed} passed, "
        f"{result.failed} failed, {result.skipped} skipped)",
        "",
        "| ID | Title | Status | Duration | Notes |",
        "|----|-------|--------|----------|-------|",
    ]
    for r in result.results:
        notes = ""
        if r.status == "failed" and r.failure is not None:
            notes = r.failure.message
        elif r.status == "error":
            notes = r.error or ""
        elif r.status == "skipped":
            notes = r.skip_reason or ""
        lines.append(
            f"| {r.test_case.id} | {r.test_case.title} | "
            f"{_emoji(r.status)} {r.status} | {r.duration_s:.2f}s | "
            f"{_md_escape(notes)} |"
        )
    if any(r.status == "failed" for r in result.results):
        lines.append("")
        lines.append("## Failures")
        for r in result.results:
            if r.status != "failed" or r.failure is None:
                continue
            lines.append("")
            lines.append(f"### {r.test_case.id} — {r.test_case.title}")
            lines.append("")
            lines.append("```")
            lines.append(r.failure.message)
            if r.failure.detail:
                lines.append("")
                lines.append(f"detail: {r.failure.detail!r}")
            if r.failure.host_event_ids:
                lines.append(f"host events: {', '.join(r.failure.host_event_ids)}")
            if r.failure.ota_event_ids:
                lines.append(f"ota events:  {', '.join(r.failure.ota_event_ids)}")
            lines.append("```")
    text = "\n".join(lines) + "\n"
    if path is not None:
        path.write_text(text, encoding="utf-8")
    return text


def _emoji(status: str) -> str:
    return {"passed": "✅", "failed": "❌", "error": "💥", "skipped": "⏭️"}.get(status, "·")


def _md_escape(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")
