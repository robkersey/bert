"""``bert scan`` — discover BLE peripherals using the HCI dongle.

Useful for figuring out what to pass to ``bert run --dut-addr`` or
``--dut-name``: just plug in the HCI dongle, run ``bert scan``, and watch
the live-updating table of nearby advertisers.
"""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.live import Live
from rich.table import Table

from bert.adapters.bumble_host import BumbleHost, ScanMatch
from bert.runner.timeline import Timeline

console = Console()


def scan_command(
    duration: float = typer.Option(
        10.0, "--duration", "-d", help="Seconds to scan for"
    ),
    name_prefix: str | None = typer.Option(
        None, "--name-prefix", help="Only show devices whose local name starts with this"
    ),
    service_uuid: str | None = typer.Option(
        None, "--service-uuid",
        help="Only show devices whose advertisement contains this service UUID (e.g. 0x180D)",
    ),
    hci_transport: str | None = typer.Option(
        None, "--hci-transport", help="Override Bumble's HCI transport string"
    ),
    sort: str = typer.Option(
        "rssi", "--sort", help="Sort key: rssi | name | address"
    ),
    quiet: bool = typer.Option(
        False, "--quiet", help="Print final table only (no live updates)"
    ),
) -> None:
    """Scan for nearby BLE peripherals and print address / name / RSSI."""

    asyncio.run(_run(duration, name_prefix, service_uuid, hci_transport, sort, quiet))


async def _run(
    duration: float,
    name_prefix: str | None,
    service_uuid: str | None,
    hci_transport: str | None,
    sort: str,
    quiet: bool,
) -> None:
    timeline = Timeline()
    seen_log: list[ScanMatch] = []

    async with BumbleHost.open(timeline=timeline, transport=hci_transport) as host:
        if quiet:
            results = await host.scan(
                duration_s=duration,
                name_prefix=name_prefix,
                service_uuid=service_uuid,
            )
            _render_final(results, sort)
            return

        # Live mode: render a table that updates as devices appear.
        with Live(
            _build_table(seen_log, sort, duration, 0.0), refresh_per_second=4, console=console
        ) as live:
            results: dict[str, ScanMatch] = {}

            def _on_seen(match: ScanMatch) -> None:
                seen_log.append(match)

            scan_task = asyncio.create_task(
                host.scan(
                    duration_s=duration,
                    name_prefix=name_prefix,
                    service_uuid=service_uuid,
                    on_seen=_on_seen,
                )
            )
            elapsed = 0.0
            while not scan_task.done():
                await asyncio.sleep(0.25)
                elapsed = min(elapsed + 0.25, duration)
                live.update(_build_table(seen_log, sort, duration, elapsed))
            results = await scan_task
            live.update(_build_table(list(results.values()), sort, duration, duration))

    if not seen_log and not quiet:
        console.print("\n[yellow]no advertising peripherals seen.[/yellow]")
        console.print(
            "  Try a longer --duration, move closer to the DUT, or check that "
            "the HCI dongle is registered (`bert dongles list`)."
        )


def _build_table(
    matches: list[ScanMatch], sort: str, total: float, elapsed: float
) -> Table:
    table = Table(
        title=f"BLE scan  ·  {elapsed:.1f}/{total:.1f}s  ·  {len(matches)} device(s)",
    )
    table.add_column("address", style="bold")
    table.add_column("name")
    table.add_column("rssi", justify="right")
    rows = sorted(matches, key=_sort_key(sort))
    for m in rows:
        bars = _rssi_bars(m.rssi)
        table.add_row(m.address, m.name or "[dim]<unnamed>[/dim]", f"{bars} {m.rssi:>4d} dBm")
    return table


def _render_final(results: dict[str, ScanMatch], sort: str) -> None:
    if not results:
        console.print("[yellow]no advertising peripherals seen[/yellow]")
        return
    table = Table(title=f"BLE scan ·  {len(results)} device(s)")
    table.add_column("address", style="bold")
    table.add_column("name")
    table.add_column("rssi", justify="right")
    for m in sorted(results.values(), key=_sort_key(sort)):
        bars = _rssi_bars(m.rssi)
        table.add_row(m.address, m.name or "[dim]<unnamed>[/dim]", f"{bars} {m.rssi:>4d} dBm")
    console.print(table)


def _sort_key(sort: str):
    if sort == "name":
        return lambda m: ((m.name or "").lower(), m.address)
    if sort == "address":
        return lambda m: m.address
    # rssi: strongest first (least negative)
    return lambda m: -(m.rssi or -200)


def _rssi_bars(rssi: int | None) -> str:
    """Crude visual bar for RSSI: stronger → longer bar."""
    if rssi is None:
        return "    "
    # -30 dBm (very strong) → 5 bars; -90 dBm → 0 bars
    bars = max(0, min(5, (rssi + 90) // 12))
    return "▰" * bars + "▱" * (5 - bars)
