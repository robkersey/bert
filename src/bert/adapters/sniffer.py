"""Drive the Nordic nRF Sniffer for Bluetooth LE.

We shell out to ``nrfutil ble-sniffer sniff`` (the Rust-based ``nrfutil``
ble-sniffer subcommand). It speaks the sniffer's UART protocol natively
and writes PCAP-NG directly to a file we pick — no Wireshark, no vendored
Python extcap script, no driver package required.

Installation prerequisite::

    nrfutil install ble-sniffer

…which also drops the matching firmware under
``$NRFUTIL_HOME/share/nrfutil-ble-sniffer/firmware/`` for ``bert flash-firmware``.

Capture lifecycle:

  * :class:`SnifferCapture.start` resolves the sniffer dongle's serial
    port, spawns ``nrfutil ble-sniffer sniff`` with the appropriate flags,
    and waits for the PCAP file to grow past zero bytes (proves the
    capture is flowing).
  * :class:`SnifferCapture.stop` sends SIGTERM, waits up to 3s, then
    SIGKILL. The PCAP file is left on disk for post-test analysis.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from bert.adapters.hci_transport import find_sniffer_dongle

log = logging.getLogger(__name__)


# Kept for backwards compatibility with `bert doctor` which used to look at a
# vendored copy of Nordic's Python extcap plugin. We no longer need it; the
# constant is exported only so existing imports don't break.
VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor" / "nrf_sniffer"


class SnifferUnavailable(RuntimeError):
    """``nrfutil ble-sniffer`` is not installed, or no sniffer dongle is attached."""


def ensure_nrfutil_ble_sniffer() -> str:
    """Return the path to a working ``nrfutil`` with the ble-sniffer subcommand.

    Raises :class:`SnifferUnavailable` with an actionable hint otherwise.
    """
    path = shutil.which("nrfutil")
    if path is None:
        raise SnifferUnavailable(
            "`nrfutil` not found on PATH. Install Nordic's nrfutil "
            "(https://www.nordicsemi.com/Products/Development-tools/nRF-Util) "
            "and then run `nrfutil install ble-sniffer`."
        )
    return path


@dataclass
class SnifferCapture:
    """Async-startable / stoppable PCAP capture handle."""

    output_path: Path
    interface: str | None = None  # serial port; auto-detected if None
    follow_address: str | None = None  # filter to packets to/from this BD_ADDR
    follow_name: str | None = None  # filter by advertised local name
    baudrate: int = 1_000_000

    _proc: asyncio.subprocess.Process | None = None
    _nrfutil_path: str | None = None

    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        nrfutil = ensure_nrfutil_ble_sniffer()
        self._nrfutil_path = nrfutil

        if self.interface is None:
            try:
                dongle = find_sniffer_dongle()
            except Exception as exc:
                raise SnifferUnavailable(str(exc)) from exc
            self.interface = dongle.device
            log.info("sniffer dongle: %s (sn=%s)", dongle.device, dongle.serial_number)

        self.output_path.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            nrfutil, "ble-sniffer", "sniff",
            "--port", self.interface,
            "--output-pcap-file", str(self.output_path),
            "--baudrate", str(self.baudrate),
        ]
        if self.follow_address:
            cmd += ["--follow", self.follow_address]
        elif self.follow_name:
            cmd += ["--follow-by-name", self.follow_name]

        log.debug("spawning sniffer: %s", " ".join(cmd))
        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Wait briefly for the PCAP file to start flowing — proves the dongle
        # is responding and packets are arriving. Don't fail if it stays empty
        # (the user might be in a quiet RF environment); just warn.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if self.output_path.exists() and self.output_path.stat().st_size > 0:
                log.info("sniffer capturing → %s", self.output_path)
                return
            if self._proc.returncode is not None:
                stderr = (await self._proc.stderr.read()).decode("utf-8", errors="replace")
                raise SnifferUnavailable(
                    f"`nrfutil ble-sniffer sniff` exited immediately "
                    f"(code {self._proc.returncode}): {stderr.strip() or '(no output)'}"
                )
            await asyncio.sleep(0.1)
        log.warning(
            "sniffer started but no packets after 5s — capture may be empty if RF "
            "is quiet, or the DUT isn't yet advertising"
        )

    async def stop(self) -> None:
        if self._proc is None:
            return
        try:
            if self._proc.returncode is None:
                self._proc.terminate()
                try:
                    await asyncio.wait_for(self._proc.wait(), timeout=3.0)
                except asyncio.TimeoutError:
                    self._proc.kill()
                    await self._proc.wait()
        finally:
            self._proc = None
