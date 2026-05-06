"""Firmware download / cache / verify."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from bert.adapters import firmware_fetch
from bert.adapters.firmware_fetch import FirmwareFetchError, FirmwareSpec


# --------------------------------------------------------------------------- #
# Manifest parsing                                                             #
# --------------------------------------------------------------------------- #


def test_load_manifest_loads_shipped_entries() -> None:
    """The wheel ships a real manifest pointing at firmware in GitHub Releases.

    We don't pin the exact contents here (the manifest is updated by
    ``scripts/update_manifest.py`` after each firmware release). Just check
    the shape: hci is present and not a placeholder, sniffer is absent
    (Bert relies on Nordic's bundled copy for that role).
    """
    manifest = firmware_fetch.load_manifest()
    assert "hci" in manifest
    spec = manifest["hci"]
    assert spec.filename.endswith(".hex"), spec.filename
    assert len(spec.sha256) == 64
    assert not spec.is_placeholder
    # sniffer has no URL in the manifest → not in the dict at all
    assert "sniffer" not in manifest


def test_manifest_url_built_from_base_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_manifest = {
        "schema_version": 1,
        "release_tag": "firmware-test",
        "base_url": "https://example.invalid/releases/download/firmware-test",
        "firmware": {
            "hci": {
                "filename": "fw.hex",
                "sha256": "a" * 64,
                "size_bytes": 100,
                "description": "test firmware",
            }
        },
    }
    fake_path = tmp_path / "manifest.json"
    fake_path.write_text(json.dumps(fake_manifest), encoding="utf-8")

    # Fake out the importlib.resources lookup.
    class _Files:
        def joinpath(self, name: str):  # noqa: ANN001 - duck-typed
            assert name == "manifest.json"
            return _Resource(fake_path)

    class _Resource:
        def __init__(self, p: Path) -> None:
            self._p = p

        def open(self, mode: str = "rb"):  # noqa: ANN001
            return self._p.open(mode)

    monkeypatch.setattr(firmware_fetch.resources, "files", lambda _pkg: _Files())

    out = firmware_fetch.load_manifest()
    assert "hci" in out
    spec = out["hci"]
    assert spec.url == "https://example.invalid/releases/download/firmware-test/fw.hex"
    assert spec.sha256 == "a" * 64
    assert not spec.is_placeholder


# --------------------------------------------------------------------------- #
# Cache                                                                        #
# --------------------------------------------------------------------------- #


def test_cache_dir_honours_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BERT_FIRMWARE_CACHE", str(tmp_path / "fwcache"))
    assert firmware_fetch.cache_dir() == tmp_path / "fwcache"


def test_cache_dir_honours_xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BERT_FIRMWARE_CACHE", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert firmware_fetch.cache_dir() == tmp_path / "xdg" / "bert" / "firmware"


def _spec(content: bytes, url: str = "https://example.invalid/fw.hex") -> FirmwareSpec:
    return FirmwareSpec(
        role="hci",
        filename="fw.hex",
        url=url,
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
    )


@pytest.fixture
def isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("BERT_FIRMWARE_CACHE", str(tmp_path / "cache"))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    return tmp_path / "cache"


def test_is_cached_false_when_absent(isolated_cache: Path) -> None:
    assert firmware_fetch.is_cached(_spec(b"hello")) is False


def test_fetch_uses_cache_when_present(isolated_cache: Path) -> None:
    spec = _spec(b"firmware-bytes")
    target = firmware_fetch.cached_path(spec)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"firmware-bytes")
    # No download attempted — would fail because URL is fake.
    assert firmware_fetch.fetch(spec) == target


def test_fetch_redownloads_on_hash_mismatch(
    isolated_cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = _spec(b"correct-bytes")
    target = firmware_fetch.cached_path(spec)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"WRONG-BYTES")  # cached file is corrupt

    def fake_download(url: str, dst: Path) -> None:
        dst.write_bytes(b"correct-bytes")

    monkeypatch.setattr(firmware_fetch, "_download_to", fake_download)
    out = firmware_fetch.fetch(spec)
    assert out == target
    assert target.read_bytes() == b"correct-bytes"


def test_fetch_raises_on_post_download_mismatch(
    isolated_cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = _spec(b"expected")

    def evil_download(url: str, dst: Path) -> None:
        dst.write_bytes(b"tampered")

    monkeypatch.setattr(firmware_fetch, "_download_to", evil_download)
    with pytest.raises(FirmwareFetchError, match="SHA256 mismatch"):
        firmware_fetch.fetch(spec)


def test_fetch_refuses_placeholder() -> None:
    spec = FirmwareSpec(
        role="hci",
        filename="fw.hex",
        url="https://github.com/REPLACE-WITH-OWNER/bert/releases/download/x/fw.hex",
        sha256=firmware_fetch.PLACEHOLDER_SHA,
    )
    with pytest.raises(FirmwareFetchError, match="placeholder"):
        firmware_fetch.fetch(spec)


def test_clear_cache_removes_files(
    isolated_cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = _spec(b"x")
    target = firmware_fetch.cached_path(spec)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"x")
    assert firmware_fetch.clear_cache() == 1
    assert not target.exists()
