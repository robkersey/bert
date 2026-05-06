"""HTML report.

Single self-contained HTML file with a timeline waterfall and per-test
detail expander. Uses Jinja2 to keep the template separate from logic.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, select_autoescape

from bert.runner.core import RunResult
from bert.runner.timeline import TimelineEvent

_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{{ md.name }} {{ md.version }} — Bert report</title>
<style>
 body { font-family: -apple-system, system-ui, sans-serif; margin: 2em; color: #222; }
 h1 { margin-bottom: 0.2em; }
 .meta { color: #666; }
 .summary { margin: 1em 0; }
 .summary span { display: inline-block; padding: 0.2em 0.6em; margin-right: 0.5em;
   border-radius: 0.3em; font-weight: 600; }
 .pass { background: #e6f6e6; color: #1c6c1c; }
 .fail { background: #fbe6e6; color: #8a1f1f; }
 .skip { background: #eef; color: #225; }
 .err  { background: #fff2cc; color: #8a5a00; }
 table { border-collapse: collapse; width: 100%; }
 th, td { padding: 0.4em 0.6em; text-align: left; border-bottom: 1px solid #eee; }
 th { background: #fafafa; }
 details { margin: 0.5em 0; }
 .timeline { font-family: ui-monospace, monospace; font-size: 0.8em; max-height: 400px;
   overflow-y: auto; background: #f7f7f7; padding: 0.5em; border-radius: 0.3em; }
 .ev-host { color: #1f4f9a; }
 .ev-ota  { color: #8a5a00; }
 code { background: #f0f0f0; padding: 0.1em 0.3em; border-radius: 0.2em; }
</style>
</head>
<body>

<h1>{{ md.name }} {{ md.version }}
  <span class="{{ overall_cls }}">{{ result.overall|upper }}</span>
</h1>
<div class="meta">
  Profile <code>{{ md.abbrev }} {{ md.version }}</code> ·
  Started {{ result.started_at.isoformat() }} ·
  {{ result.results|length }} tests
  ({{ result.passed }} passed, {{ result.failed }} failed, {{ result.skipped }} skipped)
</div>

<h2>Test cases</h2>
<table>
<thead><tr><th>ID</th><th>Title</th><th>Status</th><th>Duration</th><th>Notes</th></tr></thead>
<tbody>
{% for r in result.results %}
<tr>
  <td><code>{{ r.test_case.id }}</code></td>
  <td>{{ r.test_case.title }}</td>
  <td><span class="{{ status_cls(r.status) }}">{{ r.status }}</span></td>
  <td>{{ "%.2f"|format(r.duration_s) }}s</td>
  <td>{% if r.failure %}{{ r.failure.message }}{% elif r.error %}{{ r.error }}{% elif r.skip_reason %}{{ r.skip_reason }}{% endif %}</td>
</tr>
{% endfor %}
</tbody></table>

{% if any_failures %}
<h2>Failure detail</h2>
{% for r in result.results if r.status == "failed" and r.failure %}
<details open>
  <summary><strong>{{ r.test_case.id }}</strong> — {{ r.test_case.title }}</summary>
  <pre>{{ r.failure.message }}{% if r.failure.detail %}

detail: {{ r.failure.detail }}{% endif %}{% if r.failure.host_event_ids %}

host events: {{ r.failure.host_event_ids|join(", ") }}{% endif %}{% if r.failure.ota_event_ids %}
ota events:  {{ r.failure.ota_event_ids|join(", ") }}{% endif %}</pre>
</details>
{% endfor %}
{% endif %}

<h2>Timeline ({{ events|length }} events)</h2>
<div class="timeline">
{% for e in events %}<div class="ev-{{ e.source.value }}">{{ "%012d" % e.t_ns }} [{{ e.source.value }}] {{ e.id }} {{ e.kind }} {{ e.data }}</div>{% endfor %}
</div>

</body>
</html>
"""


def render(result: RunResult, *, path: Path | None = None) -> str:
    env = Environment(autoescape=select_autoescape(["html", "xml"]))

    def status_cls(s: str) -> str:
        return {"passed": "pass", "failed": "fail", "error": "err", "skipped": "skip"}.get(s, "")

    overall_cls = "pass" if result.overall == "passed" else "fail"
    events: list[TimelineEvent] = list(result.timeline) if result.timeline else []
    template = env.from_string(_TEMPLATE)
    text = template.render(
        result=result,
        md=result.profile.metadata,
        any_failures=any(r.status == "failed" for r in result.results),
        status_cls=status_cls,
        overall_cls=overall_cls,
        events=events,
    )
    if path is not None:
        path.write_text(text, encoding="utf-8")
    return text
