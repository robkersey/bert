"""``bert doctor`` — sanity-check the local install."""

from __future__ import annotations

import importlib
import platform
import shutil
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version

import typer
from rich.console import Console

from bert.adapters import dongle_registry
from bert.adapters.hci_transport import discover_dongles

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
    console.print("nrfutil ble-sniffer:")
    nrfutil = shutil.which("nrfutil")
    if nrfutil is None:
        console.print(
            "  [yellow]nrfutil not found on PATH[/yellow]\n"
            "  Install Nordic's nrfutil from "
            "https://www.nordicsemi.com/Products/Development-tools/nRF-Util "
            "and run `nrfutil install ble-sniffer`."
        )
        issues += 1
    else:
        probe = subprocess.run(
            [nrfutil, "ble-sniffer", "--help"], capture_output=True, text=True
        )
        if probe.returncode != 0:
            console.print(
                f"  [yellow]`nrfutil ble-sniffer` not installed[/yellow] "
                f"(at {nrfutil})\n  Run `nrfutil install ble-sniffer`."
            )
            issues += 1
        else:
            console.print(f"  [green]✓[/green] {nrfutil} ble-sniffer installed")

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
