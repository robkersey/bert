"""Bumble Device wrapper — the **only** module that imports ``bumble.*``.

Centralising Bumble usage here insulates the rest of the codebase from
Bumble's pre-1.0 API churn: when Bumble's GATT-client API moves, only this
file needs updating.

The wrapper is intentionally narrow — just the operations test cases need:
scan, connect, MTU exchange, service/characteristic discovery, read, write,
subscribe-to-notifications. Anything richer (e.g. SMP key exchange details)
is left to direct Bumble usage in profile-specific procedures, which can
import ``self._device`` if they really need to.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, AsyncIterator

from bert.adapters.hci_transport import find_hci_dongle
from bert.runner.timeline import EventSource, Timeline

if TYPE_CHECKING:
    # Bumble is imported lazily inside ``open()`` to keep import-time deps light;
    # tests can stub ``BumbleHost`` without needing Bumble installed at all.
    pass

log = logging.getLogger(__name__)


@dataclass
class ScanMatch:
    address: str
    name: str | None
    rssi: int


class BumbleHost:
    """Async context manager wrapping a Bumble Device + active connection."""

    def __init__(self, *, timeline: Timeline, passkey: int | None = None) -> None:
        self._timeline = timeline
        self._passkey = passkey
        self._device: Any = None  # bumble.device.Device
        self._transport: Any = None
        self._connection: Any = None
        self._peer: Any = None
        self._services_cache: list[str] | None = None

    # ------------------------------------------------------------------ #
    # Lifecycle                                                          #
    # ------------------------------------------------------------------ #

    @classmethod
    @asynccontextmanager
    async def open(
        cls,
        *,
        timeline: Timeline,
        transport: str | None = None,
        passkey: int | None = None,
    ) -> AsyncIterator["BumbleHost"]:
        host = cls(timeline=timeline, passkey=passkey)
        await host._start(transport)
        try:
            yield host
        finally:
            await host._stop()

    async def _start(self, transport: str | None) -> None:
        from bumble.device import Device  # type: ignore[import-not-found]
        from bumble.transport import open_transport_or_link  # type: ignore[import-not-found]

        if transport is None:
            dongle = find_hci_dongle()
            transport = dongle.transport_string()
            log.info("HCI dongle: %s (sn=%s)", dongle.device, dongle.serial_number)

        self._transport = await open_transport_or_link(transport)
        self._device = Device.with_hci(
            "bert-host",
            "F0:F1:F2:F3:F4:F5",
            self._transport.source,
            self._transport.sink,
        )
        await self._device.power_on()
        self._timeline.add(
            "host.power_on",
            EventSource.HOST,
            data={"transport": transport},
        )

    async def _stop(self) -> None:
        try:
            if self._connection is not None:
                with _suppress():
                    await self._connection.disconnect()
        finally:
            if self._transport is not None:
                with _suppress():
                    await self._transport.close()
            self._device = None
            self._transport = None
            self._connection = None
            self._peer = None

    # ------------------------------------------------------------------ #
    # Scan + connect                                                     #
    # ------------------------------------------------------------------ #

    async def scan_and_connect(
        self,
        *,
        dut_address: str | None = None,
        dut_name: str | None = None,
        scan_timeout_s: float = 10.0,
    ) -> ScanMatch:
        if not (dut_address or dut_name):
            raise ValueError("dut_address or dut_name must be provided")

        match = await self._scan_for_match(dut_address, dut_name, scan_timeout_s)
        self._timeline.add(
            "host.scan_match",
            EventSource.HOST,
            data={"address": match.address, "name": match.name, "rssi": match.rssi},
        )

        from bumble.hci import Address  # type: ignore[import-not-found]

        addr = Address(match.address)
        self._connection = await self._device.connect(addr)
        self._timeline.add(
            "host.connection_complete",
            EventSource.HOST,
            data={"peer": match.address},
        )
        try:
            mtu = await self._connection.exchange_mtu(247)
            self._timeline.add(
                "host.mtu_exchanged", EventSource.HOST, data={"mtu": mtu}
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("MTU exchange failed: %s", exc)

        from bumble.profiles.gap import Peer  # type: ignore[import-not-found]

        self._peer = Peer(self._connection)
        return match

    @staticmethod
    def _adv_local_name(adv: Any) -> str | None:
        """Extract the local name from an Advertisement, tolerating Bumble API drift.

        Bumble exposes the COMPLETE_LOCAL_NAME (0x09) and SHORTENED_LOCAL_NAME
        (0x08) AD types via ``AdvertisingData.get(type_id)`` returning a parsed
        string. Some older versions returned bytes — decode if needed.
        """
        for ad_type in (0x09, 0x08):
            try:
                val = adv.data.get(ad_type)
            except Exception:  # noqa: BLE001 - tolerate any Bumble change
                val = None
            if val is None:
                continue
            if isinstance(val, bytes):
                try:
                    return val.decode("utf-8", errors="replace")
                except Exception:  # noqa: BLE001
                    return None
            return str(val)
        return None

    async def scan(
        self,
        *,
        duration_s: float = 10.0,
        name_prefix: str | None = None,
        service_uuid: str | None = None,
        on_seen: Any = None,  # callable(ScanMatch) | None
    ) -> dict[str, ScanMatch]:
        """Passive scan for ``duration_s`` seconds; return ``address → ScanMatch``.

        The latest advertisement from each address wins (RSSI is updated).
        Optional filters:

          * ``name_prefix`` — only keep advs whose local name starts with this
          * ``service_uuid`` — only keep advs that include this UUID in their
            service-list AD field

        ``on_seen`` is called whenever a *new* device appears, so a CLI can
        render a live-updating table without waiting for the full duration.
        """

        seen: dict[str, ScanMatch] = {}

        def _on_advertisement(adv: Any) -> None:
            address = str(adv.address)
            name = self._adv_local_name(adv)
            rssi = adv.rssi
            if name_prefix and not (name or "").startswith(name_prefix):
                return
            if service_uuid:
                wanted = service_uuid.upper().lstrip("0X").lstrip("0x")
                uuids: list[str] = []
                for ad_type in (0x02, 0x03, 0x06, 0x07, 0x14, 0x15):
                    val = adv.data.get(ad_type)
                    if val:
                        uuids.append(str(val))
                if not any(wanted in u.upper() for u in uuids):
                    return
            is_new = address not in seen
            seen[address] = ScanMatch(address=address, name=name, rssi=rssi)
            if is_new and on_seen is not None:
                try:
                    on_seen(seen[address])
                except Exception:  # noqa: BLE001 - cosmetic callback must not abort scan
                    log.exception("on_seen callback raised; ignoring")

        self._device.on("advertisement", _on_advertisement)
        await self._device.start_scanning()
        try:
            await asyncio.sleep(duration_s)
        finally:
            await self._device.stop_scanning()
        return seen

    async def _scan_for_match(
        self,
        dut_address: str | None,
        dut_name: str | None,
        timeout_s: float,
    ) -> ScanMatch:
        seen: dict[str, ScanMatch] = {}
        done = asyncio.Event()

        def _on_advertisement(adv: Any) -> None:  # bumble.device.Advertisement
            address = str(adv.address)
            name = self._adv_local_name(adv)
            rssi = adv.rssi
            if dut_address and address.upper() != dut_address.upper():
                return
            if dut_name and (name or "").strip() != dut_name:
                return
            seen[address] = ScanMatch(address=address, name=name, rssi=rssi)
            done.set()

        self._device.on("advertisement", _on_advertisement)
        await self._device.start_scanning()
        try:
            try:
                await asyncio.wait_for(done.wait(), timeout=timeout_s)
            except asyncio.TimeoutError:
                pass
        finally:
            await self._device.stop_scanning()

        if not seen:
            raise RuntimeError(
                f"No advertisement matched address={dut_address!r}/name={dut_name!r} "
                f"within {timeout_s}s"
            )
        if len(seen) > 1:
            ids = ", ".join(seen)
            raise RuntimeError(
                f"Ambiguous DUT match: {ids}. Tighten --dut-addr / --dut-name."
            )
        return next(iter(seen.values()))

    # ------------------------------------------------------------------ #
    # GATT operations                                                    #
    # ------------------------------------------------------------------ #

    async def discover_services(self) -> list[str]:
        if self._services_cache is not None:
            return self._services_cache
        if self._peer is None:
            raise RuntimeError("not connected")
        services = await self._peer.discover_services()
        out = [str(s.uuid) for s in services]
        self._services_cache = out
        self._timeline.add(
            "host.services_discovered", EventSource.HOST, data={"services": out}
        )
        return out

    async def read(self, service_uuid: str, char_uuid: str) -> bytes | None:
        char = await self._find_characteristic(service_uuid, char_uuid)
        value = await char.read_value()
        self._timeline.add(
            "att.read",
            EventSource.HOST,
            data={"service": service_uuid, "char": char_uuid, "value_hex": value.hex()},
        )
        return bytes(value)

    async def write(
        self, service_uuid: str, char_uuid: str, value: bytes, *, with_response: bool = True
    ) -> None:
        char = await self._find_characteristic(service_uuid, char_uuid)
        await char.write_value(value, with_response=with_response)
        self._timeline.add(
            "att.write",
            EventSource.HOST,
            data={
                "service": service_uuid,
                "char": char_uuid,
                "value_hex": value.hex(),
                "with_response": with_response,
            },
        )

    async def subscribe_and_collect(
        self,
        service_uuid: str,
        char_uuid: str,
        *,
        duration_s: float,
    ) -> list[bytes]:
        """Subscribe to notifications/indications, collect for ``duration_s``,
        unsubscribe, return the payloads. Each notification also lands on the
        timeline as ``att.notify``.
        """

        char = await self._find_characteristic(service_uuid, char_uuid)
        received: list[bytes] = []

        def _on_value(value: bytes) -> None:
            received.append(bytes(value))
            self._timeline.add(
                "att.notify",
                EventSource.HOST,
                data={
                    "service": service_uuid,
                    "char": char_uuid,
                    "value_hex": bytes(value).hex(),
                },
            )

        await char.subscribe(_on_value)
        try:
            await asyncio.sleep(duration_s)
        finally:
            with _suppress():
                await char.unsubscribe()
        return received

    async def _find_characteristic(self, service_uuid: str, char_uuid: str) -> Any:
        if self._peer is None:
            raise RuntimeError("not connected")
        services = await self._peer.discover_services([service_uuid])
        if not services:
            raise RuntimeError(f"service {service_uuid} not present on DUT")
        chars = await services[0].discover_characteristics([char_uuid])
        if not chars:
            raise RuntimeError(f"characteristic {char_uuid} not present in {service_uuid}")
        return chars[0]


class _suppress:
    """Mini contextlib.suppress — accepts any exception and swallows it.

    Used during teardown where re-raising would mask the original error.
    """

    def __enter__(self) -> "_suppress":
        return self

    def __exit__(self, *exc: object) -> bool:  # noqa: D401
        return True

    async def __aenter__(self) -> "_suppress":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return True
