"""On-demand firmware download with SHA256 verification + local caching.

Bert ships a manifest (``src/bert/firmware/manifest.json``) listing the
expected filename, URL, and SHA256 for each role's firmware. When
``flash-firmware`` runs and no local firmware is supplied, this module:

  1. Parses the manifest.
  2. Looks for a cached copy under ``$BERT_FIRMWARE_CACHE`` (default
     ``~/.cache/bert/firmware/``) whose SHA256 matches.
  3. If absent or mismatched, downloads from the manifest URL and verifies.
  4. Returns the cached path.

Network failures fall through to a clear error message that explains how
to either manually supply the file with ``--firmware-<role>`` or build it
from source.

Cross-platform notes:
  * ``Path.home()`` resolves correctly on macOS/Linux/Windows.
  * ``$XDG_CACHE_HOME`` is honoured if set (Linux convention; harmless on
    macOS/Windows where it's typically unset).
  * ``httpx`` works identically across platforms.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

log = logging.getLogger(__name__)


PLACEHOLDER_SHA = "0" * 64
PLACEHOLDER_OWNER = "REPLACE-WITH-OWNER"


# --------------------------------------------------------------------------- #
# Manifest                                                                     #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FirmwareSpec:
    role: str
    filename: str
    url: str
    sha256: str
    size_bytes: int = 0
    description: str = ""

    @property
    def is_placeholder(self) -> bool:
        return self.sha256 == PLACEHOLDER_SHA or PLACEHOLDER_OWNER in self.url


def load_manifest() -> dict[str, FirmwareSpec]:
    """Load and parse ``manifest.json`` shipped inside the wheel.

    Returns a dict keyed by role; only entries with a usable URL +
    filename are included.
    """

    with resources.files("bert.firmware").joinpath("manifest.json").open("rb") as f:
        raw = json.load(f)

    base_url = (raw.get("base_url") or "").rstrip("/")
    out: dict[str, FirmwareSpec] = {}
    for role, entry in (raw.get("firmware") or {}).items():
        filename = entry.get("filename")
        sha = entry.get("sha256")
        if not filename or not sha:
            continue
        url = entry.get("url") or (f"{base_url}/{filename}" if base_url else None)
        if not url:
            continue
        out[role] = FirmwareSpec(
            role=role,
            filename=filename,
            url=url,
            sha256=sha,
            size_bytes=entry.get("size_bytes", 0),
            description=entry.get("description", ""),
        )
    return out


# --------------------------------------------------------------------------- #
# Cache                                                                        #
# --------------------------------------------------------------------------- #


def cache_dir() -> Path:
    """Return the firmware cache directory (creates it on demand)."""
    env = os.environ.get("BERT_FIRMWARE_CACHE")
    if env:
        return Path(env).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return base / "bert" / "firmware"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# Errors                                                                       #
# --------------------------------------------------------------------------- #


class FirmwareFetchError(RuntimeError):
    """Network failure, hash mismatch, or unconfigured manifest entry."""


# --------------------------------------------------------------------------- #
# Public API                                                                   #
# --------------------------------------------------------------------------- #


def cached_path(spec: FirmwareSpec) -> Path:
    """Where ``spec`` would live in cache (whether or not it exists yet)."""
    return cache_dir() / spec.sha256[:16] / spec.filename


def is_cached(spec: FirmwareSpec) -> bool:
    p = cached_path(spec)
    if not p.exists():
        return False
    try:
        return _sha256_file(p) == spec.sha256
    except OSError:
        return False


def fetch(spec: FirmwareSpec, *, allow_placeholder: bool = False) -> Path:
    """Return a verified local path for ``spec``, downloading if needed."""

    if spec.is_placeholder and not allow_placeholder:
        raise FirmwareFetchError(
            f"manifest entry for {spec.role!r} is a placeholder. The Bert maintainer "
            f"hasn't published this firmware yet — supply --firmware-{spec.role} <path> "
            f"with a local build, or wait for an upcoming Bert release.\n"
            f"  build hint: see manifest.json's `build_command` field"
        )

    target = cached_path(spec)
    if target.exists():
        actual = _sha256_file(target)
        if actual == spec.sha256:
            log.info("firmware %s: cache hit (%s)", spec.role, target)
            return target
        log.warning(
            "firmware %s: cached file SHA256 mismatch (got %s, expected %s); re-downloading",
            spec.role, actual[:16], spec.sha256[:16],
        )
        target.unlink()

    target.parent.mkdir(parents=True, exist_ok=True)
    _download_to(spec.url, target)
    actual = _sha256_file(target)
    if actual != spec.sha256:
        target.unlink(missing_ok=True)
        raise FirmwareFetchError(
            f"downloaded {spec.role} firmware from {spec.url} but SHA256 mismatch:\n"
            f"  expected {spec.sha256}\n"
            f"  actual   {actual}\n"
            f"This usually means the manifest is stale or the file was tampered. "
            f"Update Bert (`pip install -U bert-ble-tester`) or report a bug."
        )
    log.info("firmware %s: downloaded + verified → %s", spec.role, target)
    return target


def _download_to(url: str, target: Path) -> None:
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover
        raise FirmwareFetchError(f"httpx required for firmware download: {exc}") from exc

    log.info("downloading firmware: %s", url)
    tmp = target.with_suffix(target.suffix + ".part")
    try:
        with httpx.Client(follow_redirects=True, timeout=60.0) as client:
            with client.stream("GET", url) as resp:
                resp.raise_for_status()
                with tmp.open("wb") as f:
                    for chunk in resp.iter_bytes():
                        f.write(chunk)
        tmp.replace(target)
    except httpx.HTTPError as exc:
        tmp.unlink(missing_ok=True)
        raise FirmwareFetchError(
            f"could not download firmware from {url}: {exc}\n"
            f"If you're offline, supply --firmware-<role> with a local copy."
        ) from exc


def clear_cache() -> int:
    """Delete every cached firmware file. Returns the count removed."""
    cache = cache_dir()
    if not cache.exists():
        return 0
    n = 0
    for p in cache.rglob("*"):
        if p.is_file():
            p.unlink()
            n += 1
    return n
