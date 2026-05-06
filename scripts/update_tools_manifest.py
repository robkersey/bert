#!/usr/bin/env python3
"""Update src/bert/firmware/manifest.json's `tools` section from a Release.

Pairs with `.github/workflows/build-dfu-tools.yml`. After that workflow has
uploaded one binary per platform to a GitHub Release tagged
``dfu-tools-YYYY.MM.DD``, run this script to fetch each, hash it, and
rewrite the tools section of the manifest.

Example:
    scripts/update_tools_manifest.py \\
        --owner robkersey --repo bert \\
        --tag dfu-tools-2026.05.06
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


PLATFORM_FILES = {
    "darwin-arm64": "bert-dfu-darwin-arm64",
    "darwin-x86_64": "bert-dfu-darwin-x86_64",
    "linux-x86_64": "bert-dfu-linux-x86_64",
    "windows-x86_64": "bert-dfu-windows-x86_64.exe",
}


def gh_release_download(owner: str, repo: str, tag: str, asset: str, dest: Path) -> Path | None:
    if shutil.which("gh") is None:
        raise SystemExit("the GitHub CLI (`gh`) is required: https://cli.github.com/")
    dest.mkdir(parents=True, exist_ok=True)
    cmd = [
        "gh", "release", "download", tag,
        "--repo", f"{owner}/{repo}",
        "--pattern", asset,
        "--dir", str(dest),
        "--clobber",
    ]
    print("$ " + " ".join(cmd), flush=True)
    rc = subprocess.run(cmd).returncode
    if rc != 0:
        return None
    out = dest / asset
    return out if out.exists() else None


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
    ap.add_argument("--tag", required=True)
    args = ap.parse_args()

    base_url = f"https://github.com/{args.owner}/{args.repo}/releases/download/{args.tag}"

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    tools = manifest.setdefault("tools", {})
    bert_dfu = tools.setdefault("bert-dfu", {})
    bert_dfu["release_tag"] = args.tag
    bert_dfu["base_url"] = base_url
    platforms = bert_dfu.setdefault("platforms", {})

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        for plat, asset in PLATFORM_FILES.items():
            local = gh_release_download(args.owner, args.repo, args.tag, asset, td_path)
            if local is None:
                print(f"  [skip] no asset {asset} for {plat}")
                continue
            sha, size = sha256_file(local)
            print(f"  {plat}: {asset}  sha256={sha[:16]}…  size={size}")
            platforms[plat] = {
                "filename": asset,
                "sha256": sha,
                "size_bytes": size,
            }

    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(f"updated {MANIFEST_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
