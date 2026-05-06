"""``bert tools`` — manage helper-binary cache.

Helper binaries (currently just ``bert-dfu``, a single-file build of
adafruit-nrfutil) are downloaded from GitHub Releases on demand by
``bert flash-firmware``. This subcommand lets you pre-fetch / verify /
clear that cache.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from bert.adapters import firmware_fetch

app = typer.Typer(no_args_is_help=True, help="Manage helper-binary cache.")
console = Console()


@app.command(name="list")
def list_cmd() -> None:
    """Show every tool entry the manifest knows about for this host platform."""

    plat = firmware_fetch.host_platform_tag()
    tools = firmware_fetch.load_tool_manifest()
    if not tools:
        console.print(
            f"[yellow]no tools available for platform {plat!r}[/yellow]\n"
            f"Either you're on an unsupported architecture or the maintainer "
            f"hasn't published binaries for this platform yet."
        )
        return

    table = Table(title=f"Helper binaries for {plat}  →  {firmware_fetch.tool_cache_dir()}")
    table.add_column("name")
    table.add_column("filename")
    table.add_column("status")
    table.add_column("sha256 (prefix)")
    table.add_column("description", overflow="fold")
    for name, spec in tools.items():
        if spec.is_placeholder:
            status = "[yellow]placeholder[/yellow]"
        elif firmware_fetch.is_tool_cached(spec):
            status = "[green]cached[/green]"
        else:
            status = "[blue]not cached[/blue]"
        table.add_row(
            name, spec.filename, status, spec.sha256[:16] + "…", spec.description or ""
        )
    console.print(table)


@app.command()
def download(
    name: str = typer.Option(
        "all", "--name", help="Tool name (e.g. bert-dfu) or 'all'"
    ),
    force: bool = typer.Option(False, "--force", help="Re-download even if cached"),
) -> None:
    """Pre-fetch helper binaries (e.g. before going offline)."""

    tools = firmware_fetch.load_tool_manifest()
    if not tools:
        console.print(
            f"[yellow]no tools available for platform "
            f"{firmware_fetch.host_platform_tag()!r}[/yellow]"
        )
        raise typer.Exit(code=1)

    targets = list(tools) if name == "all" else [name]
    fail = 0
    for n in targets:
        spec = tools.get(n)
        if spec is None:
            console.print(f"[red]✗[/red] {n}: not in manifest for this platform")
            fail += 1
            continue
        if spec.is_placeholder:
            console.print(
                f"[yellow]·[/yellow] {n}: placeholder in manifest "
                f"(maintainer hasn't published this one yet); skipping"
            )
            continue
        if force:
            target = firmware_fetch.cached_tool_path(spec)
            if target.exists():
                target.unlink()
        try:
            path = firmware_fetch.fetch_tool(spec)
        except firmware_fetch.FirmwareFetchError as exc:
            console.print(f"[red]✗[/red] {n}: {exc}")
            fail += 1
            continue
        console.print(f"[green]✓[/green] {n}: {path}")

    if fail:
        raise typer.Exit(code=1)


@app.command()
def verify() -> None:
    """Re-hash every cached tool and confirm against the manifest."""

    tools = firmware_fetch.load_tool_manifest()
    bad = 0
    for n, spec in tools.items():
        if spec.is_placeholder:
            continue
        path = firmware_fetch.cached_tool_path(spec)
        if not path.exists():
            console.print(f"[blue]·[/blue] {n}: not cached")
            continue
        ok = firmware_fetch.is_tool_cached(spec)
        marker = "[green]✓[/green]" if ok else "[red]✗[/red]"
        console.print(f"{marker} {n}: {path}")
        if not ok:
            bad += 1
    if bad:
        console.print(
            f"[red]{bad} cached tool(s) failed verification.[/red] "
            f"Run `bert tools download --force` to re-fetch."
        )
        raise typer.Exit(code=1)


@app.command(name="clear-cache")
def clear_cache_cmd() -> None:
    """Delete every cached tool binary."""
    n = firmware_fetch.clear_tool_cache()
    console.print(f"removed {n} file(s) from {firmware_fetch.tool_cache_dir()}")
