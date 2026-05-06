"""Interactive review of a draft IR.

The default UX is a simple terminal prompt: walk every needs_review node,
show its source-anchor + extracted values, accept / edit / reject. A richer
Textual app can come later — the current interface is enough to clear flags
on a draft, which is the gate the runner enforces.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.prompt import Confirm, Prompt

from bert.ir import dump_yaml, load_yaml
from bert.ir.schema import Profile, Reviewable

console = Console()


def run_review(*, input_path: Path, output_path: Path) -> None:
    profile = load_yaml(input_path)
    flagged = _walk(profile)
    if not flagged:
        console.print("[green]nothing to review.[/green]")
        _stamp_and_save(profile, output_path)
        return

    console.print(f"[bold]{len(flagged)}[/bold] node(s) flagged needs_review.\n")
    for path, node in flagged:
        console.rule(path)
        if node.source_anchor:
            console.print(f"  source: [italic]{node.source_anchor}[/italic]")
        console.print(f"  confidence: {node.confidence:.2f}")
        console.print(f"  extracted: {_summarise(node)}")
        choice = Prompt.ask(
            "  accept / reject / skip",
            choices=["a", "r", "s"],
            default="a",
        )
        if choice == "a":
            node.needs_review = False
            node.confidence = max(node.confidence, 0.9)
        elif choice == "r":
            console.print(
                "  [yellow]rejected — node will remain flagged. Edit the YAML manually then re-run.[/yellow]"
            )
        # "s" leaves the flag unchanged

    _stamp_and_save(profile, output_path)


def _walk(profile: Profile) -> list[tuple[str, Reviewable]]:
    out: list[tuple[str, Reviewable]] = []

    def visit(obj: Any, path: str) -> None:
        if isinstance(obj, Reviewable) and obj.needs_review:
            out.append((path or "<root>", obj))
        if hasattr(obj, "model_fields"):
            for fname in obj.__class__.model_fields:
                visit(getattr(obj, fname), f"{path}.{fname}" if path else fname)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                visit(item, f"{path}[{i}]")

    visit(profile, "")
    return out


def _summarise(node: Reviewable) -> str:
    data = node.model_dump(exclude={"confidence", "needs_review", "source_anchor"})
    return ", ".join(f"{k}={v!r}" for k, v in data.items() if v not in (None, [], {}))


def _stamp_and_save(profile: Profile, output_path: Path) -> None:
    if Confirm.ask(
        "  set reviewer + reviewed_at and save?", default=True
    ):
        if profile.metadata.reviewer is None:
            profile.metadata.reviewer = Prompt.ask("    reviewer email", default="reviewer@example.com")
        profile.metadata.reviewed_at = date.today()
    dump_yaml(profile, output_path)
    console.print(f"[green]wrote[/green] {output_path}")
