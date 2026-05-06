"""Shared CLI helpers."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import typer

from bert.ir import Profile, load_packaged, load_yaml


def load_profile(profile: str) -> Profile:
    """Resolve ``profile`` as either a bundled name (``heart-rate``) or a path."""

    if "/" in profile or profile.endswith((".yaml", ".yml")) or Path(profile).exists():
        return load_yaml(profile)
    name = profile.replace("-", "_")
    try:
        return load_packaged(name)
    except (ModuleNotFoundError, FileNotFoundError) as exc:
        typer.secho(
            f"unknown profile {profile!r}; pass a YAML path or one of the bundled names",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=2) from exc


def list_bundled_profiles() -> list[str]:
    out: list[str] = []
    pkg = resources.files("bert.profiles")
    for entry in pkg.iterdir():
        if not entry.is_dir():
            continue
        if entry.name.startswith("_") or entry.name == "__pycache__":
            continue
        if (entry / "profile.yaml").is_file():
            out.append(entry.name.replace("_", "-"))
    return sorted(out)
