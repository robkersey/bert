"""``bert ir`` — author and review profile IR specs."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from bert.ir import IRLoadError, dump_yaml, load_yaml
from bert.ir.validate import assert_runnable

app = typer.Typer(no_args_is_help=True, help="Profile IR commands.")
console = Console()


@app.command()
def parse(
    source: str = typer.Argument(..., help="URL or local path to a SIG profile doc (HTML or PDF)."),
    out: Path = typer.Option(Path("profile.draft.yaml"), "--out", "-o"),
) -> None:
    """Extract a draft IR from a SIG profile document.

    The output is intentionally **never** runnable: the parser flags ambiguous
    extractions with ``needs_review: true``, and the runner refuses to load
    drafts. Use ``bert ir review`` next to clear those flags.
    """

    from bert.parser import parse_source

    profile = parse_source(source)
    dump_yaml(profile, out)
    flagged = profile.unreviewed_nodes()
    console.print(f"[green]wrote[/green] {out}")
    console.print(f"  {len(flagged)} node(s) flagged needs_review")
    if flagged:
        for path in flagged[:10]:
            console.print(f"    · {path}")
        if len(flagged) > 10:
            console.print(f"    … +{len(flagged) - 10} more")
    console.print(f"\nNext: `bert ir review {out}` to clear flags.")


@app.command()
def review(
    draft: Path = typer.Argument(..., help="Path to draft YAML (produced by `bert ir parse`)"),
    out: Path = typer.Option(..., "--out", "-o", help="Path to write reviewed YAML"),
) -> None:
    """Walk every needs_review node in an interactive Textual TUI."""

    from bert.review.tui import run_review

    run_review(input_path=draft, output_path=out)


@app.command()
def validate(
    profile_yaml: Path = typer.Argument(...),
    allow_draft: bool = typer.Option(False, "--allow-draft"),
) -> None:
    """Load + semantically validate a profile YAML."""

    try:
        profile = load_yaml(profile_yaml)
        assert_runnable(profile, allow_draft=allow_draft)
    except IRLoadError as exc:
        console.print(f"[red]invalid:[/red] {exc}")
        raise typer.Exit(code=1) from None
    console.print(
        f"[green]valid[/green]: {profile.metadata.name} {profile.metadata.version} "
        f"— {len(profile.test_cases)} test case(s)"
    )


@app.command()
def diff(a: Path = typer.Argument(...), b: Path = typer.Argument(...)) -> None:
    """Semantic diff between two profile YAMLs."""

    from bert.review.diff import diff_profiles

    pa = load_yaml(a)
    pb = load_yaml(b)
    changes = diff_profiles(pa, pb)
    if not changes:
        console.print("[green]identical[/green]")
        return
    for change in changes:
        console.print(change)
