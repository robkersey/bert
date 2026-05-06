"""``bert profiles`` — list bundled profile suites."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from bert.cli._helpers import list_bundled_profiles, load_profile

app = typer.Typer(no_args_is_help=True, help="Bundled profile commands.")
console = Console()


@app.command(name="list")
def list_cmd() -> None:
    """List every profile suite shipped with Bert."""
    table = Table(title="Bundled profiles")
    table.add_column("name")
    table.add_column("title")
    table.add_column("version")
    table.add_column("tests")
    for name in list_bundled_profiles():
        try:
            p = load_profile(name)
        except Exception as exc:  # noqa: BLE001
            table.add_row(name, f"[red]load error: {exc}[/red]", "", "")
            continue
        table.add_row(name, p.metadata.name, p.metadata.version, str(len(p.test_cases)))
    console.print(table)


@app.command()
def show(name: str = typer.Argument(...)) -> None:
    """Pretty-print a bundled profile's metadata + test cases."""
    p = load_profile(name)
    console.print(f"[bold]{p.metadata.name}[/bold] {p.metadata.version} ({p.metadata.abbrev})")
    console.print(f"  source: {p.metadata.source_doc}")
    console.print(f"  reviewer: {p.metadata.reviewer}  reviewed: {p.metadata.reviewed_at}")
    console.print(f"  services: {len(p.services)}  tests: {len(p.test_cases)}")
    for tc in p.test_cases:
        console.print(f"    · {tc.id}  {tc.title}  (procedure={tc.procedure}, source={tc.source.value})")
