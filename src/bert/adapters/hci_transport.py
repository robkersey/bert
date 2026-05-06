"""Discover the HCI dongle and build a Bumble transport string.

We do NOT use the host OS Bluetooth stack. The HCI controller is a Nordic
nRF52840 USB dongle flashed with Zephyr's ``hci_uart`` sample, which exposes
itself as a USB-CDC virtual serial port. Bumble's ``serial:`` transport
talks raw HCI over that COM port.

Stock Zephyr / Nordic firmware doesn't set a Bert-specific USB descriptor,
so we identify dongles by their factory USB iSerialNumber via the registry
written at flash time (see :mod:`bert.adapters.dongle_registry`):

    Nordic VID:    0x1915
    matched by:    factory iSerialNumber  →  registry  →  role

A dongle is only considered usable if a matching role entry exists in
``~/.config/bert/dongles.json``. Bert-flashed dongles whose serial numbers
were registered under the legacy ``BERT-HCI-`` / ``BERT-SNF-`` prefix
scheme are still recognised — we keep that path as a fallback.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from serial.tools import list_ports

from bert.adapters import dongle_registry

log = logging.getLogger(__name__)

NORDIC_VID = 0x1915
HCI_SERIAL_PREFIX = "BERT-HCI-"  # legacy: kept for backward-compat
SNIFFER_SERIAL_PREFIX = "BERT-SNF-"
DEFAULT_BAUD = 1_000_000


class DongleNotFound(RuntimeError):
    """No matching dongle could be located on USB."""


@dataclass(frozen=True)
class Dongle:
    role: str  # "hci" | "sniffer"
    device: str  # /dev/cu.usbmodemXXXX or COMn
    serial_number: str
    description: str
    vid: int
    pid: int

    def transport_string(self, baud: int = DEFAULT_BAUD) -> str:
        return f"serial:{self.device},{baud}"


def _role_from_sn(sn: str) -> str | None:
    """Look up ``sn`` in the registry, falling back to legacy prefix matching."""
    role = dongle_registry.role_for_serial(sn)
    if role:
        return role
    if sn.startswith(HCI_SERIAL_PREFIX):
        return "hci"
    if sn.startswith(SNIFFER_SERIAL_PREFIX):
        return "sniffer"
    return None


def discover_dongles() -> list[Dongle]:
    """Return all Bert-registered Nordic dongles currently attached."""
    out: list[Dongle] = []
    for p in list_ports.comports():
        vid = getattr(p, "vid", None)
        if vid != NORDIC_VID:
            continue
        sn = (getattr(p, "serial_number", "") or "").strip()
        if not sn:
            continue
        role = _role_from_sn(sn)
        if role is None:
            log.debug(
                "Nordic dongle on %s (sn=%s) not registered as hci/sniffer; "
                "run `bert flash-firmware` to register it",
                p.device,
                sn,
            )
            continue
        out.append(
            Dongle(
                role=role,
                device=p.device,
                serial_number=sn,
                description=p.description or "",
                vid=vid,
                pid=p.pid or 0,
            )
        )
    return out


def find_hci_dongle() -> Dongle:
    matches = [d for d in discover_dongles() if d.role == "hci"]
    if not matches:
        raise DongleNotFound(
            "No HCI controller dongle found. Plug in a Bert-flashed nRF52840 dongle "
            "or run `bert flash-firmware --dongle hci` first."
        )
    if len(matches) > 1:
        devices = ", ".join(d.device for d in matches)
        raise DongleNotFound(
            f"Multiple HCI dongles found ({devices}). Disconnect the spare or "
            f"pass --hci-transport explicitly."
        )
    return matches[0]


def find_sniffer_dongle() -> Dongle:
    matches = [d for d in discover_dongles() if d.role == "sniffer"]
    if not matches:
        raise DongleNotFound(
            "No sniffer dongle found. Plug in a Bert-flashed nRF52840 dongle "
            "with the nRF Sniffer firmware, or run `bert flash-firmware --dongle sniffer`."
        )
    if len(matches) > 1:
        devices = ", ".join(d.device for d in matches)
        raise DongleNotFound(
            f"Multiple sniffer dongles found ({devices}). Disconnect the spare."
        )
    return matches[0]


def detect_misflashed_hci_usb() -> list[str]:
    """Placeholder: detect any nRF52840 exposing the BT-class HCI USB transport.

    Zephyr's ``hci_usb`` sample presents USB class 0xE0 and is silently grabbed
    by macOS ``bluetoothd``. Detecting that requires a libusb scan, which we
    don't do today; we rely on the absence of a registered dongle to surface
    the problem.
    """
    return []
