"""HTML parser smoke + confidence flagging."""

from __future__ import annotations

from bert.parser.confidence import score_requirement
from bert.parser.html_parser import parse_html


def test_score_requirement_strong_mandatory() -> None:
    c, needs = score_requirement("the device shall expose this characteristic")
    assert c >= 0.9 and not needs


def test_score_requirement_optional() -> None:
    c, needs = score_requirement("optional, if supported")
    assert c >= 0.8


def test_score_requirement_ambiguous_marks_for_review() -> None:
    c, needs = score_requirement("nondescript text without keywords")
    assert needs is True


def test_parse_html_extracts_uuids() -> None:
    html = b"""
    <html><head><title>Test Profile</title></head><body>
    <h2>Services</h2>
    <table>
      <tr><th>Service</th><th>UUID</th><th>Support</th></tr>
      <tr><td>Heart Rate</td><td>0x180D</td><td>Mandatory</td></tr>
      <tr><td>Device Information</td><td>0x180A</td><td>Mandatory</td></tr>
    </table>
    <h2>Characteristics</h2>
    <table>
      <tr><th>Characteristic</th><th>UUID</th><th>Properties</th><th>Support</th></tr>
      <tr><td>Heart Rate Measurement</td><td>0x2A37</td><td>Notify</td><td>Mandatory</td></tr>
    </table>
    </body></html>
    """
    profile = parse_html(html, source_url="local-test", sha256="deadbeef")
    uuids = {s.uuid for s in profile.services}
    assert "0x180D" in uuids
    assert "0x180A" in uuids
    # The first service should have the characteristic attached.
    assert any(c.uuid == "0x2A37" for s in profile.services for c in s.characteristics)
