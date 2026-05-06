#!/usr/bin/env python3
"""Update src/bert/firmware/manifest.json from a published GitHub Release.

Maintainer workflow:

  1. The build-firmware GHA runs and uploads <tag>/hci_uart_nrf52840dongle.hex
     to a GitHub Release.
  2. Run this script to fetch the asset, hash it, and rewrite manifest.json
     with the new SHA256 + URL.
  3. Commit, open a PR, and cut a Bert release.

Example:
    scripts/update_manifest.py \\
        --owner robkersey --repo bert \\
        --tag firmware-2026.05.06

Implementation note: we shell out to ``gh release download`` rather than
fetching the asset over HTTPS directly. ``gh`` uses the system trust store
and the user's existing GitHub auth, sidestepping cert issues with bundled
Pythons (e.g. PlatformIO's Python lacks ``certifi``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MANIFEST_PATH = (
    Path(__file__).resolve().parent.parent
    / "src" / "bert" / "firmware" / "manifest.json"
)


def gh_release_download(owner: str, repo: str, tag: str, asset: str, dest: Path) -> Path:
    if shutil.which("gh") is None:
        raise SystemExit(
            "the GitHub CLI (`gh`) is required: https://cli.github.com/"
        )
    dest.mkdir(parents=True, exist_ok=True)
    cmd = [
        "gh", "release", "download", tag,
        "--repo", f"{owner}/{repo}",
        "--pattern", asset,
        "--dir", str(dest),
        "--clobber",
    ]
    print("$ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)
    out = dest / asset
    if not out.exists():
        raise SystemExit(
            f"gh release download succeeded but expected file not present: {out}"
        )
    return out


def sha256_file(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--owner", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--tag", required=True, help="Release tag (e.g. firmware-2026.05.06)")
    ap.add_argument(
        "--asset", default="hci_uart_nrf52840dongle.hex",
        help="Asset filename in the release",
    )
    args = ap.parse_args()

    base_url = (
        f"https://github.com/{args.owner}/{args.repo}/releases/download/{args.tag}"
    )

    with tempfile.TemporaryDirectory() as td:
        local = gh_release_download(
            args.owner, args.repo, args.tag, args.asset, Path(td)
        )
        sha, size = sha256_file(local)

    print(f"  sha256 = {sha}")
    print(f"  size   = {size} bytes")

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest["release_tag"] = args.tag
    manifest["base_url"] = base_url
    manifest["firmware"]["hci"]["filename"] = args.asset
    manifest["firmware"]["hci"]["sha256"] = sha
    manifest["firmware"]["hci"]["size_bytes"] = size

    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(f"updated {MANIFEST_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
