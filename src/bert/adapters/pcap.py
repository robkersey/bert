"""Read a PCAP-NG file and fold its packets into Bert's timeline.

Two link-types are accepted:

  * **DLT_NORDIC_BLE (272)** — what ``nrfutil ble-sniffer`` writes. Decoded
    in pure Python by :mod:`bert.adapters.pcap_nordic`. The Nordic-specific
    per-packet header is stripped, and the inner BLE LL packet is examined.
  * **DLT_BLUETOOTH_LE_LL (251) / _WITH_PHDR (256)** — the SIG-standardised
    DLTs Wireshark + scapy understand. We hand these to scapy's
    ``bluetooth4LE`` layer if it's available.

Mapping packets → timeline events is intentionally coarse (advertising,
data PDU, ATT notify/indicate, MTU). Procedures that need finer detail can
re-parse the PCAP themselves.
"""

from __future__ import annotations

import logging
from pathlib import Path

from bert.adapters import pcap_nordic
from bert.runner.timeline import EventSource, Timeline

log = logging.getLogger(__name__)


def fold_pcap_into_timeline(pcap_path: Path, timeline: Timeline) -> int:
    """Read ``pcap_path`` and append events to ``timeline``. Returns event count."""

    if not pcap_path.exists():
        log.warning("pcap file %s does not exist; skipping fold", pcap_path)
        return 0

    count = 0
    by_linktype: dict[int, int] = {}
    try:
        for linktype, raw, t_ns in pcap_nordic.iter_packets(pcap_path):
            by_linktype[linktype] = by_linktype.get(linktype, 0) + 1
            event = _classify(linktype, raw)
            if event is None:
                continue
            kind, data = event
            timeline.add(kind, EventSource.OTA, data=data, t_ns=t_ns)
            count += 1
    except pcap_nordic.PcapNgParseError as exc:
        log.error("could not parse %s as PCAP-NG: %s", pcap_path, exc)
        return 0
    log.info(
        "folded %d / %d packet(s) from %s; linktypes=%s",
        count,
        sum(by_linktype.values()),
        pcap_path,
        by_linktype,
    )
    return count


def _classify(linktype: int, raw: bytes) -> tuple[str, dict] | None:
    """Map a (linktype, raw-bytes) pair to a (kind, data) timeline event."""

    if linktype == pcap_nordic.DLT_NORDIC_BLE:
        return pcap_nordic.parse_nordic_packet(raw)

    if linktype in (
        pcap_nordic.DLT_BLUETOOTH_LE_LL,
        pcap_nordic.DLT_BLUETOOTH_LE_LL_WITH_PHDR,
    ):
        return _classify_via_scapy(raw)

    log.debug("ignoring packet with unsupported linktype %d", linktype)
    return None


def _classify_via_scapy(raw: bytes) -> tuple[str, dict] | None:
    """Hand a SIG-DLT packet to scapy's bluetooth4LE layer."""

    try:
        from scapy.layers.bluetooth4LE import (  # type: ignore[import-not-found]
            BTLE,
            BTLE_ADV,
            BTLE_DATA,
        )
    except Exception as exc:  # pragma: no cover
        log.warning("scapy bluetooth4LE not available, skipping packet: %s", exc)
        return None

    pkt = BTLE(raw)
    if pkt.haslayer(BTLE_ADV):
        adv = pkt.getlayer(BTLE_ADV)
        return "btle.adv", {
            "adva": getattr(adv, "AdvA", None),
            "pdu_type": getattr(adv, "PDU_type", None),
        }
    if pkt.haslayer(BTLE_DATA):
        try:
            from scapy.layers.bluetooth import (  # type: ignore[import-not-found]
                ATT_Exchange_MTU_Request,
                ATT_Exchange_MTU_Response,
                ATT_Handle_Value_Indication,
                ATT_Handle_Value_Notification,
            )
        except Exception:  # pragma: no cover
            return "btle.data", {}
        if pkt.haslayer(ATT_Handle_Value_Notification):
            notif = pkt.getlayer(ATT_Handle_Value_Notification)
            return "att.notify_ota", {"handle": getattr(notif, "gatt_handle", None)}
        if pkt.haslayer(ATT_Handle_Value_Indication):
            ind = pkt.getlayer(ATT_Handle_Value_Indication)
            return "att.indicate_ota", {"handle": getattr(ind, "gatt_handle", None)}
        if pkt.haslayer(ATT_Exchange_MTU_Request):
            return "att.mtu_req", {}
        if pkt.haslayer(ATT_Exchange_MTU_Response):
            mtu = pkt.getlayer(ATT_Exchange_MTU_Response)
            return "att.mtu_rsp", {"mtu": getattr(mtu, "server_rx_mtu", None)}
        return "btle.data", {}
    return "btle.other", {}
