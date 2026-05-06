"""Persistent mapping of USB serial number → Bert role.

Off-the-shelf Zephyr ``hci_uart`` and Nordic Sniffer firmware images don't
set a Bert-specific USB iSerialNumber, so we can't tell them apart by USB
descriptors alone. Instead, when ``bert flash-firmware`` succeeds for a
given role, we record:

    {"role": "hci", "serial_number": "F4CE36ABCDEF"}

in ``~/.config/bert/dongles.json``. The dongle's USB iSerialNumber is set
by the factory and survives across firmware flashes, so it's a stable key.

Discovery (:mod:`bert.adapters.hci_transport`) reads this registry first;
attached Nordic dongles whose serial numbers aren't registered are ignored.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


def registry_path() -> Path:
    base = os.environ.get("BERT_CONFIG_DIR")
    if base:
        return Path(base) / "dongles.json"
    return Path.home() / ".config" / "bert" / "dongles.json"


@dataclass(frozen=True)
class RegisteredDongle:
    role: str  # "hci" | "sniffer"
    serial_number: str


def load() -> list[RegisteredDongle]:
    path = registry_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        log.warning("dongle registry %s is corrupt (%s); ignoring", path, exc)
        return []
    return [
        RegisteredDongle(role=d["role"], serial_number=d["serial_number"])
        for d in data.get("dongles", [])
        if d.get("role") in {"hci", "sniffer"} and d.get("serial_number")
    ]


def save(entries: list[RegisteredDongle]) -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "dongles": [
            {"role": e.role, "serial_number": e.serial_number} for e in entries
        ],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def upsert(role: str, serial_number: str) -> None:
    """Record ``serial_number`` as the dongle for ``role``.

    If ``serial_number`` is already registered (under any role), its row is
    updated. If a different serial number is already registered for ``role``,
    the previous row is replaced.
    """

    entries = [
        e for e in load()
        if e.serial_number != serial_number and e.role != role
    ]
    entries.append(RegisteredDongle(role=role, serial_number=serial_number))
    save(entries)
    log.info("registered %s dongle: %s", role, serial_number)


def role_for_serial(serial_number: str) -> str | None:
    for e in load():
        if e.serial_number == serial_number:
            return e.role
    return None
