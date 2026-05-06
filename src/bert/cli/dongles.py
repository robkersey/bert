"""``bert dongles`` — list, init, flash."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from bert.adapters.flasher import FlashError, flash_all
from bert.adapters.hci_transport import discover_dongles

app = typer.Typer(no_args_is_help=True, help="Manage dongles.")
console = Console()


@app.command(name="list")
def list_cmd() -> None:
    """List Bert-flashed dongles currently attached."""
    dongles = discover_dongles()
    if not dongles:
        console.print("[yellow]no Bert-flashed Nordic dongles found[/yellow]")
        return
    table = Table()
    table.add_column("role")
    table.add_column("device")
    table.add_column("serial")
    table.add_column("description")
    for d in dongles:
        table.add_row(d.role, d.device, d.serial_number, d.description)
    console.print(table)


def init_dongles() -> None:
    """Interactive: detect attached dongles and confirm both roles came up."""
    dongles = discover_dongles()
    if not dongles:
        console.print("[yellow]no Bert-flashed dongles attached.[/yellow]")
        console.print("Plug in a dongle and run `bert flash-firmware` first.")
        raise typer.Exit(code=1)
    list_cmd()
    have_hci = any(d.role == "hci" for d in dongles)
    have_snf = any(d.role == "sniffer" for d in dongles)
    if have_hci and have_snf:
        console.print("[green]ready[/green] — HCI + sniffer both detected.")
        return
    missing: list[str] = []
    if not have_hci:
        missing.append("hci")
    if not have_snf:
        missing.append("sniffer")
    console.print(f"[yellow]missing:[/yellow] {', '.join(missing)}")
    raise typer.Exit(code=1)


def flash_firmware(
    dongle: str = typer.Option(
        "all", "--dongle", help="hci | sniffer | all"
    ),
    firmware_hci: Path | None = typer.Option(
        None, "--firmware-hci", help="Override the HCI .hex path"
    ),
    firmware_sniffer: Path | None = typer.Option(
        None, "--firmware-sniffer", help="Override the sniffer .hex path"
    ),
) -> None:
    """Flash one or both dongles via Nordic ``nrfutil``.

    The flow per dongle is interactive:

      1. You're prompted to plug in the dongle and press its RESET button to
         enter the Open Bootloader (the small button on the side of the
         nRF52840 USB Dongle, not the white one).
      2. Bert detects the bootloader on USB and runs ``nrfutil pkg generate``
         + ``nrfutil dfu usb-serial`` to write the .hex.
      3. Bert waits for the dongle to re-enumerate as the application device.
      4. Repeat for the next role.

    Requires the legacy Python ``nrfutil`` (``pip install nrfutil``). The
    Rust-based ``nrfutil device`` does NOT support the dongle's USB DFU.
    """
    if dongle not in {"hci", "sniffer", "all"}:
        console.print(f"[red]invalid --dongle: {dongle}[/red]")
        raise typer.Exit(code=2)

    roles = ["hci", "sniffer"] if dongle == "all" else [dongle]

    try:
        asyncio.run(
            flash_all(
                roles=roles,
                firmware_hci=firmware_hci,
                firmware_sniffer=firmware_sniffer,
            )
        )
    except FlashError as exc:
        console.print(f"[red]flash failed:[/red] {exc}")
        raise typer.Exit(code=1) from None
    except KeyboardInterrupt:
        console.print("\n[yellow]aborted[/yellow]")
        raise typer.Exit(code=130) from None
