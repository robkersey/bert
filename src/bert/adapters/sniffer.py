"""Drive the Nordic nRF Sniffer for Bluetooth LE.

Two backends:

* ``extcap`` (default) — spawn Nordic's vendored Wireshark extcap plugin
  (``nrf_sniffer_ble.py``) as a subprocess. It writes PCAP-NG to a file we
  pick. We don't need Wireshark on the user's machine; we only need the
  extcap script + its companion modules, which we vendor under
  ``src/bert/vendor/nrf_sniffer/``.

* ``direct`` (``--sniffer-backend=direct``, future) — talk SLIP-over-UART
  to the dongle ourselves. Smaller dependency surface, but means we own
  the protocol-versioning treadmill. Kept as a stub here.

Both backends produce PCAP-NG that ``adapters/pcap.py`` later folds into
the timeline.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from bert.adapters.hci_transport import find_sniffer_dongle

log = logging.getLogger(__name__)


VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor" / "nrf_sniffer"


class SnifferUnavailable(RuntimeError):
    """No sniffer backend could be initialised on this machine."""


@dataclass
class SnifferCapture:
    """Async-startable / stoppable PCAP capture handle."""

    output_path: Path
    interface: str | None = None  # filled in from dongle discovery if None
    backend: str = "extcap"

    _proc: asyncio.subprocess.Process | None = None

    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        if self.backend != "extcap":
            raise SnifferUnavailable(
                f"sniffer backend {self.backend!r} not implemented yet; "
                f"use the default 'extcap' backend"
            )

        if self.interface is None:
            dongle = find_sniffer_dongle()
            self.interface = dongle.device
            log.info("sniffer dongle: %s (sn=%s)", dongle.device, dongle.serial_number)

        extcap_script = _locate_extcap_script()
        if extcap_script is None:
            raise SnifferUnavailable(
                "vendored Nordic nRF Sniffer extcap plugin not found under "
                f"{VENDOR_DIR}. Run `bert flash-firmware` to install it, or "
                "install the Nordic nRF Sniffer for Bluetooth LE manually."
            )

        self.output_path.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            sys.executable,
            str(extcap_script),
            "--capture",
            "--extcap-interface",
            f"COM-{self.interface}",
            "--fifo",
            str(self.output_path),
        ]
        log.debug("spawning sniffer: %s", " ".join(cmd))
        env = os.environ.copy()
        # Make sure the vendored module path is importable for the extcap script.
        env["PYTHONPATH"] = str(VENDOR_DIR) + os.pathsep + env.get("PYTHONPATH", "")
        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        # Wait for the PCAP file to grow past zero bytes — that proves the
        # capture is actually flowing. Bail after 5s if nothing appears.
        deadline = asyncio.get_event_loop().time() + 5.0
        while asyncio.get_event_loop().time() < deadline:
            if self.output_path.exists() and self.output_path.stat().st_size > 0:
                return
            await asyncio.sleep(0.1)
        log.warning(
            "sniffer started but no packets after 5s — capture may still be empty"
        )

    async def stop(self) -> None:
        if self._proc is None:
            return
        try:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                self._proc.kill()
                await self._proc.wait()
        finally:
            self._proc = None


def _locate_extcap_script() -> Path | None:
    candidates = [
        VENDOR_DIR / "nrf_sniffer_ble.py",
        VENDOR_DIR / "extcap" / "nrf_sniffer_ble.py",
    ]
    for c in candidates:
        if c.exists():
            return c
    # Fall back to a system-wide install if the user has Nordic's tools installed.
    found = shutil.which("nrf_sniffer_ble.py")
    return Path(found) if found else None
