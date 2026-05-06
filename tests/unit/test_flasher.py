"""Flasher: firmware path resolution + nrfutil presence check.

End-to-end flashing requires hardware, the legacy Python nrfutil, and a real
dongle in DFU mode — those tests live under ``tests/hwil/``. Here we cover
the deterministic helpers, including macOS- and Windows-style paths.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from bert.adapters import flasher


# --------------------------------------------------------------------------- #
# Override path                                                                #
# --------------------------------------------------------------------------- #


def test_resolve_firmware_uses_override_when_present(tmp_path: Path) -> None:
    fw = tmp_path / "custom.hex"
    fw.write_text(":00000001FF\n")
    assert flasher.resolve_firmware("hci", fw) == fw


def test_resolve_firmware_uses_zip_override(tmp_path: Path) -> None:
    fw = tmp_path / "custom.zip"
    fw.write_bytes(b"PK\x03\x04")  # zip magic; no need for a real archive
    assert flasher.resolve_firmware("sniffer", fw) == fw


def test_resolve_firmware_rejects_missing_override(tmp_path: Path) -> None:
    with pytest.raises(flasher.FlashError, match="not found"):
        flasher.resolve_firmware("hci", tmp_path / "nope.hex")


# --------------------------------------------------------------------------- #
# Bundled-firmware path (the wheel ships hex/zip under src/bert/firmware/)     #
# --------------------------------------------------------------------------- #


def test_resolve_firmware_explains_when_nothing_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_pkg_root = tmp_path / "pkg"
    (fake_pkg_root / "adapters").mkdir(parents=True)
    (fake_pkg_root / "firmware").mkdir()
    monkeypatch.setattr(
        flasher, "__file__", str(fake_pkg_root / "adapters" / "flasher.py")
    )
    monkeypatch.setenv("NRFUTIL_HOME", str(tmp_path / "no-nrfutil-here"))
    # allow_download=False so we don't fall through to the manifest fetcher
    # (which would actually succeed against the real shipped manifest).
    with pytest.raises(flasher.FlashError, match="no firmware found"):
        flasher.resolve_firmware("hci", None, allow_download=False)


# --------------------------------------------------------------------------- #
# Nordic ble-sniffer firmware auto-discovery                                   #
# --------------------------------------------------------------------------- #


@pytest.fixture
def fake_nrfutil_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point NRFUTIL_HOME at a tmp dir and clear any in-wheel firmware bundle."""
    home = tmp_path / "fake-nrfutil"
    monkeypatch.setenv("NRFUTIL_HOME", str(home))

    fake_pkg_root = tmp_path / "pkg"
    (fake_pkg_root / "adapters").mkdir(parents=True)
    (fake_pkg_root / "firmware").mkdir()
    monkeypatch.setattr(
        flasher, "__file__", str(fake_pkg_root / "adapters" / "flasher.py")
    )
    return home


def test_nrfutil_home_honours_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NRFUTIL_HOME", str(tmp_path / "elsewhere"))
    assert flasher.nrfutil_home() == tmp_path / "elsewhere"


def test_nrfutil_home_defaults_to_dot_nrfutil(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NRFUTIL_HOME", raising=False)
    assert flasher.nrfutil_home() == Path.home() / ".nrfutil"


def test_resolve_firmware_finds_nordic_sniffer_zip(fake_nrfutil_home: Path) -> None:
    fw_dir = fake_nrfutil_home / "share" / "nrfutil-ble-sniffer" / "firmware"
    fw_dir.mkdir(parents=True)
    target = fw_dir / "sniffer_nrf52840dongle_nrf52840_4.1.1.zip"
    target.write_bytes(b"PK\x03\x04")
    # The DK variants (.hex) must NOT be selected for the dongle role.
    (fw_dir / "sniffer_nrf52840dk_nrf52840_4.1.1.hex").write_text(":00000001FF\n")

    resolved = flasher.resolve_firmware("sniffer", None)
    assert resolved == target


def test_resolve_firmware_picks_highest_sniffer_version(fake_nrfutil_home: Path) -> None:
    fw_dir = fake_nrfutil_home / "share" / "nrfutil-ble-sniffer" / "firmware"
    fw_dir.mkdir(parents=True)
    (fw_dir / "sniffer_nrf52840dongle_nrf52840_4.0.0.zip").write_bytes(b"PK\x03\x04")
    target = fw_dir / "sniffer_nrf52840dongle_nrf52840_4.1.1.zip"
    target.write_bytes(b"PK\x03\x04")
    assert flasher.resolve_firmware("sniffer", None) == target


def test_hci_role_does_not_use_nordic_sniffer_dir(fake_nrfutil_home: Path) -> None:
    fw_dir = fake_nrfutil_home / "share" / "nrfutil-ble-sniffer" / "firmware"
    fw_dir.mkdir(parents=True)
    (fw_dir / "sniffer_nrf52840dongle_nrf52840_4.1.1.zip").write_bytes(b"PK\x03\x04")
    with pytest.raises(flasher.FlashError, match="no firmware found"):
        flasher.resolve_firmware("hci", None, allow_download=False)


# --------------------------------------------------------------------------- #
# Download tier (manifest → firmware_fetch)                                    #
# --------------------------------------------------------------------------- #


def test_resolve_firmware_uses_manifest_download(
    fake_nrfutil_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no local firmware is found, resolve_firmware delegates to firmware_fetch."""
    from bert.adapters import firmware_fetch as ff

    monkeypatch.setenv("BERT_FIRMWARE_CACHE", str(tmp_path / "cache"))

    fake = ff.FirmwareSpec(
        role="hci",
        filename="hci.hex",
        url="https://example.invalid/hci.hex",
        sha256="b" * 64,
    )
    monkeypatch.setattr(ff, "load_manifest", lambda: {"hci": fake})

    sentinel = tmp_path / "sentinel.hex"
    sentinel.write_text(":00000001FF\n")
    monkeypatch.setattr(ff, "fetch", lambda spec: sentinel)

    assert flasher.resolve_firmware("hci", None) == sentinel


def test_resolve_firmware_skips_placeholder_manifest(
    fake_nrfutil_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bert.adapters import firmware_fetch as ff

    placeholder = ff.FirmwareSpec(
        role="hci",
        filename="hci.hex",
        url="https://github.com/REPLACE-WITH-OWNER/bert/releases/download/x/hci.hex",
        sha256=ff.PLACEHOLDER_SHA,
    )
    monkeypatch.setattr(ff, "load_manifest", lambda: {"hci": placeholder})
    with pytest.raises(flasher.FlashError, match="no firmware found"):
        flasher.resolve_firmware("hci", None)


def test_resolve_firmware_allow_download_false_skips_network(
    fake_nrfutil_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bert.adapters import firmware_fetch as ff

    def boom() -> dict:
        raise AssertionError("manifest must NOT be consulted when allow_download=False")

    monkeypatch.setattr(ff, "load_manifest", boom)
    with pytest.raises(flasher.FlashError, match="no firmware found"):
        flasher.resolve_firmware("hci", None, allow_download=False)


# --------------------------------------------------------------------------- #
# Tooling check                                                                #
# --------------------------------------------------------------------------- #


def test_ensure_nrfutil_complains_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    from bert.adapters import firmware_fetch

    monkeypatch.setattr(flasher.shutil, "which", lambda _name: None)
    monkeypatch.setattr(firmware_fetch, "load_tool_manifest", lambda: {})
    with pytest.raises(flasher.FlashError, match="no usable DFU tool"):
        flasher.ensure_nrfutil(allow_download=False)


def test_ensure_nrfutil_prefers_cached_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bert.adapters import firmware_fetch

    fake = tmp_path / "bert-dfu"
    fake.write_text("#!/bin/sh\necho ok\n")
    fake.chmod(0o755)

    spec = firmware_fetch.ToolSpec(
        name="bert-dfu",
        platform="darwin-arm64",
        filename="bert-dfu",
        url="https://example.invalid/bert-dfu",
        sha256="a" * 64,
    )
    monkeypatch.setattr(firmware_fetch, "load_tool_manifest", lambda: {"bert-dfu": spec})
    monkeypatch.setattr(firmware_fetch, "is_tool_cached", lambda s: True)
    monkeypatch.setattr(firmware_fetch, "cached_tool_path", lambda s: fake)
    # Pretend nothing else is on PATH so the cached binary wins.
    monkeypatch.setattr(flasher.shutil, "which", lambda _name: None)
    # Probe always returns adafruit dialect.
    monkeypatch.setattr(
        flasher,
        "_probe_nrfutil",
        lambda p: flasher.DfuTool(path=p, dialect="adafruit"),
    )
    tool = flasher.ensure_nrfutil()
    assert tool.path == str(fake)
    assert tool.dialect == "adafruit"


def test_ensure_nrfutil_falls_back_to_path_adafruit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bert.adapters import firmware_fetch

    monkeypatch.setattr(firmware_fetch, "load_tool_manifest", lambda: {})

    def fake_which(name: str) -> str | None:
        if name == "adafruit-nrfutil":
            return "/usr/local/bin/adafruit-nrfutil"
        return None

    monkeypatch.setattr(flasher.shutil, "which", fake_which)
    monkeypatch.setattr(
        flasher,
        "_probe_nrfutil",
        lambda p: flasher.DfuTool(path=p, dialect="adafruit"),
    )
    tool = flasher.ensure_nrfutil(allow_download=False)
    assert tool.path == "/usr/local/bin/adafruit-nrfutil"


def test_dfu_tool_command_dialects() -> None:
    """The two CLI dialects produce the expected argv shape."""
    leg = flasher.DfuTool(path="nrfutil", dialect="legacy-python")
    ada = flasher.DfuTool(path="adafruit-nrfutil", dialect="adafruit")
    assert leg.dfu_serial_args("p.zip", "/dev/cu.X") == [
        "nrfutil", "dfu", "usb-serial", "-pkg", "p.zip", "-p", "/dev/cu.X"
    ]
    assert ada.dfu_serial_args("p.zip", "/dev/cu.X") == [
        "adafruit-nrfutil", "dfu", "serial", "--package", "p.zip", "--port", "/dev/cu.X"
    ]
    # pkg generate flags are identical between dialects.
    assert leg.pkg_generate_args("a.hex", "p.zip")[1:3] == ["pkg", "generate"]
    assert ada.pkg_generate_args("a.hex", "p.zip")[1:3] == ["pkg", "generate"]
