"""``bert run`` — execute a profile against a DUT."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import typer
from rich.console import Console

from bert.cli._helpers import load_profile
from bert.reporter import write_all
from bert.runner.core import RunConfig, Runner

console = Console()


def run_command(
    profile: str = typer.Option(..., "--profile", help="Bundled profile name (e.g. heart-rate) or path to profile.yaml"),
    dut_addr: str | None = typer.Option(None, "--dut-addr", help="DUT BLE address (e.g. AA:BB:CC:DD:EE:FF)"),
    dut_name: str | None = typer.Option(None, "--dut-name", help="DUT advertised local name"),
    passkey: int | None = typer.Option(None, "--passkey", help="Pairing passkey if needed"),
    report_dir: Path = typer.Option(Path("runs"), "--report-dir", help="Where to write run artefacts"),
    report_format: str = typer.Option(
        "junit,markdown,html",
        "--report-format",
        help="Comma-separated subset of: junit,markdown,html",
    ),
    repeats: int = typer.Option(1, "--repeats", min=1, help="Repeat each test case N times (best-of-K via --quorum)"),
    quorum: int = typer.Option(1, "--quorum", min=1, help="Required passing repetitions per test"),
    sniffer: bool = typer.Option(True, "--sniffer/--no-sniffer", help="Enable Nordic sniffer capture"),
    hci_transport: str | None = typer.Option(None, "--hci-transport", help="Override Bumble HCI transport string"),
    allow_draft: bool = typer.Option(False, "--allow-draft", help="Allow profile YAML with needs_review:true left"),
) -> None:
    if not (dut_addr or dut_name):
        console.print("[red]error:[/red] one of --dut-addr or --dut-name is required.")
        raise typer.Exit(code=2)

    p = load_profile(profile)
    cfg = RunConfig(
        profile=p,
        dut_address=dut_addr,
        dut_name=dut_name,
        passkey=passkey,
        report_dir=report_dir,
        repeats=repeats,
        quorum=quorum,
        sniffer_enabled=sniffer,
        hci_transport=hci_transport,
        allow_draft=allow_draft,
    )

    runner = Runner(cfg)
    result = asyncio.run(runner.run())

    formats = {f.strip() for f in report_format.split(",") if f.strip()}
    written = write_all(
        result,
        run_dir=report_dir / result.started_at.strftime("%Y%m%dT%H%M%SZ"),
        formats=formats,
    )

    console.print()
    for r in result.results:
        marker = {
            "passed": "[green]✓[/green]",
            "failed": "[red]✗[/red]",
            "error": "[yellow]![/yellow]",
            "skipped": "[blue]—[/blue]",
        }.get(r.status, "?")
        line = f"{marker} {r.test_case.id} {r.test_case.title} ({r.duration_s:.2f}s)"
        if r.failure:
            line += f"\n    {r.failure.message}"
        console.print(line)
    console.print()
    console.print(
        f"[bold]{result.overall.upper()}[/bold]  "
        f"{result.passed} passed · {result.failed} failed · {result.skipped} skipped"
    )
    for fmt, path in written.items():
        console.print(f"  {fmt}: {path}")

    raise typer.Exit(code=0 if result.overall == "passed" else 1)


def main() -> None:  # pragma: no cover
    sys.argv[0] = "bert run"
    typer.run(run_command)
