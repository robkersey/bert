"""``bert doctor`` — sanity-check the local install."""

from __future__ import annotations

import importlib
import platform
import sys
from importlib.metadata import PackageNotFoundError, version

import typer
from rich.console import Console

from bert.adapters import dongle_registry
from bert.adapters.hci_transport import discover_dongles
from bert.adapters.sniffer import VENDOR_DIR

console = Console()


def doctor() -> None:
    """Verify python, dependencies, dongles, vendored sniffer plugin."""

    issues = 0

    console.print("[bold]Bert doctor[/bold]")
    console.print(f"  python: {sys.version.split()[0]} on {platform.platform()}")

    issues += _check_pkg("bumble", min_version="0.0.200")
    issues += _check_pkg("scapy", min_version="2.5")
    issues += _check_pkg("pydantic", min_version="2.6")
    issues += _check_pkg("pyserial", import_name="serial")
    issues += _check_pkg("typer", min_version="0.12")

    console.print()
    reg = dongle_registry.load()
    console.print(f"Registry ({dongle_registry.registry_path()}):")
    if not reg:
        console.print("  [yellow]empty — run `bert flash-firmware` to register dongles[/yellow]")
    else:
        for entry in reg:
            console.print(f"  [green]✓[/green] {entry.role:<7} sn={entry.serial_number}")

    console.print()
    console.print("Dongles attached:")
    dongles = discover_dongles()
    if not dongles:
        console.print("  [yellow]no Bert-flashed Nordic dongles detected[/yellow]")
        console.print("  Plug in two nRF52840 dongles and run `bert flash-firmware`.")
        issues += 1
    else:
        for d in dongles:
            console.print(
                f"  [green]✓[/green] {d.role:<7} {d.device}  sn={d.serial_number}  ({d.description})"
            )
        roles = {d.role for d in dongles}
        if "hci" not in roles:
            console.print("  [yellow]missing role: hci[/yellow]")
            issues += 1
        if "sniffer" not in roles:
            console.print("  [yellow]missing role: sniffer[/yellow]")
            issues += 1

    console.print()
    console.print("Vendored sniffer plugin:")
    extcap_candidates = [
        VENDOR_DIR / "nrf_sniffer_ble.py",
        VENDOR_DIR / "extcap" / "nrf_sniffer_ble.py",
    ]
    found = next((c for c in extcap_candidates if c.exists()), None)
    if found:
        console.print(f"  [green]✓[/green] {found}")
    else:
        console.print(f"  [yellow]not vendored at {VENDOR_DIR}[/yellow]")
        console.print("  Run `bert flash-firmware` to install (it bundles the extcap plugin).")
        issues += 1

    console.print()
    if issues:
        console.print(f"[red]{issues} issue(s) detected[/red]")
        raise typer.Exit(code=1)
    console.print("[green]all checks passed[/green]")


def _check_pkg(name: str, *, import_name: str | None = None, min_version: str | None = None) -> int:
    """Print one dependency check; return 1 if missing/too old, else 0."""
    try:
        importlib.import_module(import_name or name)
    except ImportError:
        console.print(f"  [red]✗[/red] {name} not installed")
        return 1
    try:
        v = version(name)
    except PackageNotFoundError:
        v = "?"
    if min_version is not None and v != "?":
        if _vt(v) < _vt(min_version):
            console.print(f"  [yellow]![/yellow] {name} {v} (need ≥ {min_version})")
            return 1
    console.print(f"  [green]✓[/green] {name} {v}")
    return 0


def _vt(s: str) -> tuple[int, ...]:
    parts = []
    for p in s.replace("+", ".").split("."):
        try:
            parts.append(int(p))
        except ValueError:
            break
    return tuple(parts) or (0,)
