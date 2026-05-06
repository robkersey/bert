"""YAML <-> Profile loading."""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from bert.ir.schema import Profile


class IRLoadError(RuntimeError):
    """Raised when the IR fails schema validation or the unreviewed-nodes gate."""


def load_yaml(path: str | Path) -> Profile:
    """Load and validate a profile YAML from disk."""

    raw = Path(path).read_text(encoding="utf-8")
    data = yaml.safe_load(raw) or {}
    return Profile.model_validate(data)


def dump_yaml(profile: Profile, path: str | Path | None = None) -> str:
    """Serialise a Profile to YAML. Returns the YAML string; writes to ``path`` if given."""

    data: dict[str, Any] = profile.model_dump(mode="json", exclude_none=True)
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    if path is not None:
        Path(path).write_text(text, encoding="utf-8")
    return text


def load_packaged(profile_name: str) -> Profile:
    """Load a profile shipped inside the package, e.g. ``"heart_rate"``."""

    pkg = f"bert.profiles.{profile_name}"
    with resources.files(pkg).joinpath("profile.yaml").open("rb") as f:
        data = yaml.safe_load(f.read()) or {}
    return Profile.model_validate(data)
