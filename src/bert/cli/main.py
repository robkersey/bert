"""Top-level Bert CLI."""

from __future__ import annotations

import logging

import typer

from bert.cli import dongles as dongles_cli
from bert.cli import doctor as doctor_cli
from bert.cli import firmware as firmware_cli
from bert.cli import ir as ir_cli
from bert.cli import pcap as pcap_cli
from bert.cli import profiles as profiles_cli
from bert.cli import run as run_cli
from bert.cli import tools as tools_cli

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Bert — BLE profile compliance tester.",
)

app.add_typer(ir_cli.app, name="ir", help="Author and review profile IR specs.")
app.add_typer(profiles_cli.app, name="profiles", help="List bundled profiles.")
app.add_typer(dongles_cli.app, name="dongles", help="Manage dongles (init, flash, list).")
app.add_typer(firmware_cli.app, name="firmware", help="Manage prebuilt firmware images.")
app.add_typer(tools_cli.app, name="tools", help="Manage helper-binary cache (bert-dfu).")
app.add_typer(pcap_cli.app, name="pcap", help="Offline PCAP analysis.")
app.command(name="run", help="Run a profile against a DUT.")(run_cli.run_command)
app.command(name="doctor", help="Verify the local install.")(doctor_cli.doctor)


@app.callback()
def _root(verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


# Convenience for ``bert flash-firmware`` and ``bert init-dongles`` to live at
# the top level even though their implementations sit under ``dongles``.
app.command(name="init-dongles")(dongles_cli.init_dongles)
app.command(name="flash-firmware")(dongles_cli.flash_firmware)


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
