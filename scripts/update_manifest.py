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
        --owner phazeone --repo bert \\
        --tag firmware-2026.05.06
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import urllib.request

MANIFEST_PATH = Path(__file__).resolve().parent.parent / "src" / "bert" / "firmware" / "manifest.json"


def fetch_and_hash(url: str) -> tuple[str, int]:
    print(f"GET {url}", flush=True)
    h = hashlib.sha256()
    size = 0
    with urllib.request.urlopen(url) as resp:  # noqa: S310 - URL is operator-supplied
        while chunk := resp.read(1024 * 1024):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--owner", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--tag", required=True, help="Release tag (e.g. firmware-2026.05.06)")
    ap.add_argument(
        "--asset", default="hci_uart_nrf52840dongle.hex",
        help="Asset filename in the release",
    )
    args = ap.parse_args()

    base_url = f"https://github.com/{args.owner}/{args.repo}/releases/download/{args.tag}"
    asset_url = f"{base_url}/{args.asset}"

    sha, size = fetch_and_hash(asset_url)
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
