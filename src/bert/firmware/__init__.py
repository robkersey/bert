"""Firmware payloads + manifest shipped inside the wheel.

The actual ``.hex`` / ``.zip`` payloads are NOT shipped in the wheel — they
live in GitHub Releases, pinned by SHA256 in :file:`manifest.json`. The
:mod:`bert.adapters.firmware_fetch` module downloads them on demand and
caches under ``~/.cache/bert/firmware/``.
"""
