"""``bert firmware`` — manage the local firmware cache.

Useful for:

  * pre-fetching firmware on a machine that's about to be offline
    (``bert firmware download``);
  * inspecting what Bert thinks the canonical firmware is
    (``bert firmware list``);
  * recovering from a corrupt cache (``bert firmware clear-cache``).
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from bert.adapters import firmware_fetch

app = typer.Typer(no_args_is_help=True, help="Manage prebuilt firmware images.")
console = Console()


@app.command(name="list")
def list_cmd() -> None:
    """List every firmware entry the manifest knows about and its cache state."""

    manifest = firmware_fetch.load_manifest()
    if not manifest:
        console.print("[yellow]manifest is empty (no firmware declared)[/yellow]")
        return

    table = Table(title=f"Firmware manifest  →  cache: {firmware_fetch.cache_dir()}")
    table.add_column("role")
    table.add_column("filename")
    table.add_column("status")
    table.add_column("sha256 (prefix)")
    table.add_column("description", overflow="fold")
    for role, spec in manifest.items():
        if spec.is_placeholder:
            status = "[yellow]placeholder[/yellow]"
        elif firmware_fetch.is_cached(spec):
            status = "[green]cached[/green]"
        else:
            status = "[blue]not cached[/blue]"
        table.add_row(
            role,
            spec.filename,
            status,
            spec.sha256[:16] + "…",
            spec.description or "",
        )
    console.print(table)


@app.command()
def download(
    role: str = typer.Option(
        "all", "--role", help="hci | sniffer | all"
    ),
    force: bool = typer.Option(False, "--force", help="Re-download even if cached"),
) -> None:
    """Pre-fetch firmware (handy before going offline)."""

    manifest = firmware_fetch.load_manifest()
    roles = list(manifest) if role == "all" else [role]
    if not roles:
        console.print(f"[red]no firmware in manifest for role {role!r}[/red]")
        raise typer.Exit(code=2)

    fail = 0
    for r in roles:
        spec = manifest.get(r)
        if spec is None:
            console.print(f"[yellow]·[/yellow] {r}: no manifest entry, skipping")
            continue
        if spec.is_placeholder:
            console.print(
                f"[yellow]·[/yellow] {r}: placeholder in manifest "
                f"(maintainer hasn't published this one yet); skipping"
            )
            continue
        if force:
            target = firmware_fetch.cached_path(spec)
            if target.exists():
                target.unlink()
        try:
            path = firmware_fetch.fetch(spec)
        except firmware_fetch.FirmwareFetchError as exc:
            console.print(f"[red]✗[/red] {r}: {exc}")
            fail += 1
            continue
        console.print(f"[green]✓[/green] {r}: {path}")

    if fail:
        raise typer.Exit(code=1)


@app.command()
def verify() -> None:
    """Re-hash every cached firmware and confirm against the manifest."""

    manifest = firmware_fetch.load_manifest()
    bad = 0
    for r, spec in manifest.items():
        if spec.is_placeholder:
            continue
        path = firmware_fetch.cached_path(spec)
        if not path.exists():
            console.print(f"[blue]·[/blue] {r}: not cached")
            continue
        ok = firmware_fetch.is_cached(spec)
        marker = "[green]✓[/green]" if ok else "[red]✗[/red]"
        console.print(f"{marker} {r}: {path}")
        if not ok:
            bad += 1
    if bad:
        console.print(
            f"[red]{bad} cached file(s) failed verification.[/red] "
            f"Run `bert firmware download --force` to re-fetch."
        )
        raise typer.Exit(code=1)


@app.command(name="clear-cache")
def clear_cache_cmd() -> None:
    """Delete every cached firmware file."""
    n = firmware_fetch.clear_cache()
    console.print(f"removed {n} file(s) from {firmware_fetch.cache_dir()}")
