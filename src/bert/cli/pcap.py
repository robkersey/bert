"""``bert pcap`` — offline PCAP analysis."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from bert.adapters.pcap import fold_pcap_into_timeline
from bert.cli._helpers import load_profile
from bert.runner.timeline import Timeline

app = typer.Typer(no_args_is_help=True, help="Offline PCAP analysis.")
console = Console()


@app.command()
def analyse(
    pcap: Path = typer.Argument(..., exists=True, readable=True),
    against: str = typer.Option(..., "--against", help="Profile name or YAML path"),
) -> None:
    """Re-run OTA-source assertions from a recorded PCAP, no DUT needed.

    Useful for triaging a flaky test from CI — the run wrote
    ``capture.pcapng``, this command re-evaluates the OTA assertions against
    a (possibly newer) profile spec.
    """

    profile = load_profile(against)
    timeline = Timeline()
    n = fold_pcap_into_timeline(pcap, timeline)
    console.print(f"folded {n} packets from {pcap}")
    ota_cases = [tc for tc in profile.test_cases if tc.source.value in {"ota", "both"}]
    console.print(f"{len(ota_cases)} OTA test case(s) to re-evaluate")
    # NOTE: actual re-execution is wired into the runner in v0.2; for now we
    # surface the timeline so a user can poke around.
    for ev in list(timeline)[:20]:
        console.print(f"  {ev.t_ns}  {ev.kind}  {ev.data}")
    if len(timeline) > 20:
        console.print(f"  … and {len(timeline) - 20} more events")
