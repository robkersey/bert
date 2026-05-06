"""Persistent dongle registry round-trip + idempotent upsert."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from bert.adapters import dongle_registry


@pytest.fixture
def isolated_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BERT_CONFIG_DIR", str(tmp_path))
    yield tmp_path
    # cleanup is automatic via tmp_path
    _ = os  # silence unused-import in some lint configs


def test_empty_registry_returns_no_entries(isolated_registry: Path) -> None:
    assert dongle_registry.load() == []
    assert dongle_registry.role_for_serial("anything") is None


def test_upsert_writes_and_loads_back(isolated_registry: Path) -> None:
    dongle_registry.upsert("hci", "F4CE36AAA")
    dongle_registry.upsert("sniffer", "F4CE36BBB")

    entries = dongle_registry.load()
    roles = {e.role: e.serial_number for e in entries}
    assert roles == {"hci": "F4CE36AAA", "sniffer": "F4CE36BBB"}

    assert dongle_registry.role_for_serial("F4CE36AAA") == "hci"
    assert dongle_registry.role_for_serial("F4CE36BBB") == "sniffer"
    assert dongle_registry.role_for_serial("nope") is None


def test_upsert_replaces_existing_role(isolated_registry: Path) -> None:
    dongle_registry.upsert("hci", "OLD")
    dongle_registry.upsert("hci", "NEW")
    entries = dongle_registry.load()
    assert len(entries) == 1
    assert entries[0].serial_number == "NEW"


def test_upsert_replaces_existing_serial(isolated_registry: Path) -> None:
    """If the same dongle is re-flashed for a different role, the old row goes."""
    dongle_registry.upsert("hci", "F4CE36AAA")
    dongle_registry.upsert("sniffer", "F4CE36AAA")
    entries = dongle_registry.load()
    assert len(entries) == 1
    assert entries[0].role == "sniffer"


def test_corrupt_file_returns_empty(isolated_registry: Path) -> None:
    path = dongle_registry.registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert dongle_registry.load() == []
