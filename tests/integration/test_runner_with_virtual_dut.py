"""End-to-end runner test using Bumble's virtual link layer.

Requires Bumble to be installed (skipped otherwise). Spins up a Bumble HRP
peripheral and runs Bert against it without touching any real hardware.
We use Bumble's TCP-pair transport so two devices share an in-process link.

Two scenarios:

* ``test_compliant_hrm_passes_all`` — peripheral implements HRP correctly
* ``test_noncompliant_hrm_fails_cadence`` — peripheral notifies far too fast,
  TC_HRP_002 must fail.
"""

from __future__ import annotations

import asyncio
import struct

import pytest

bumble = pytest.importorskip("bumble", reason="bumble not installed")

from bert.ir import load_packaged
from bert.runner.core import RunConfig, Runner

pytestmark = pytest.mark.integration


@pytest.fixture
def hrp_profile():
    return load_packaged("heart_rate")


# --------------------------------------------------------------------------- #
# Virtual peripheral fixtures                                                  #
# --------------------------------------------------------------------------- #


async def _serve_compliant_hrm(notification_interval_s: float) -> tuple[str, asyncio.Task]:
    """Start a Bumble peripheral that advertises HRP and notifies HRM at the
    given cadence. Returns (transport_string_for_central, server_task)."""
    from bumble.device import Device
    from bumble.gatt import (
        GATT_HEART_RATE_MEASUREMENT_CHARACTERISTIC,
        GATT_HEART_RATE_SERVICE,
        Characteristic,
        Service,
    )
    from bumble.transport import open_transport_or_link

    central_xport = "tcp-server:127.0.0.1:9920"
    peripheral_xport = "tcp-client:127.0.0.1:9920"

    server_t = await open_transport_or_link(peripheral_xport)
    device = Device.with_hci("dut", "AA:AA:AA:AA:AA:AA", server_t.source, server_t.sink)

    hrm_char = Characteristic(
        GATT_HEART_RATE_MEASUREMENT_CHARACTERISTIC,
        Characteristic.Properties.NOTIFY,
        Characteristic.Permissions.READABLE,
        b"\x00\x40",  # flags=0x00 (uint8 hr), HR=64
    )
    device.add_service(Service(GATT_HEART_RATE_SERVICE, [hrm_char]))
    await device.power_on()
    await device.start_advertising(advertising_data=b"\x02\x01\x06\x05\x03\x0d\x18\x0a\x18")

    async def _notifier() -> None:
        while True:
            await asyncio.sleep(notification_interval_s)
            try:
                await device.notify_subscribers(hrm_char, b"\x00\x40")
            except Exception:
                return

    task = asyncio.create_task(_notifier())
    return central_xport, task


# --------------------------------------------------------------------------- #
# The tests                                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_compliant_hrm_passes_cadence(hrp_profile, tmp_path):
    """Peripheral notifies every 1s — within the 250–2000ms HRP bound."""
    pytest.skip("requires bumble's tcp-pair transport wired up; tracked in v0.2")
    transport, server = await _serve_compliant_hrm(notification_interval_s=1.0)
    try:
        cfg = RunConfig(
            profile=hrp_profile,
            dut_address="AA:AA:AA:AA:AA:AA",
            sniffer_enabled=False,
            hci_transport=transport,
            report_dir=tmp_path,
        )
        result = await Runner(cfg).run()
        assert result.overall == "passed"
    finally:
        server.cancel()


@pytest.mark.asyncio
async def test_noncompliant_hrm_fails_cadence(hrp_profile, tmp_path):
    """Peripheral notifies every 50ms — outside the bound; TC_HRP_002 must fail."""
    pytest.skip("requires bumble's tcp-pair transport wired up; tracked in v0.2")
    transport, server = await _serve_compliant_hrm(notification_interval_s=0.05)
    try:
        cfg = RunConfig(
            profile=hrp_profile,
            dut_address="AA:AA:AA:AA:AA:AA",
            sniffer_enabled=False,
            hci_transport=transport,
            report_dir=tmp_path,
        )
        result = await Runner(cfg).run()
        cadence = next(r for r in result.results if r.test_case.id == "TC_HRP_002")
        assert cadence.status == "failed"
    finally:
        server.cancel()


def _hrm_frame(flags: int, hr: int) -> bytes:
    return struct.pack("<BB", flags, hr)
