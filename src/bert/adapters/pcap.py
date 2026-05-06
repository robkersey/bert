"""Read a PCAP-NG file and fold its packets into Bert's timeline.

We use scapy's ``bluetooth4LE`` layer rather than tshark/pyshark to keep
end-user dependencies tight: scapy is pure-Python (with optional libpcap
acceleration) and gives us native Python objects rather than parsed XML.

The mapping from packet → timeline event is intentionally coarse for v1:

  * Advertising / scan-response   → ``btle.adv``        with adva, type
  * Connection request            → ``btle.connect_req`` with peer
  * ATT Exchange MTU Req / Rsp    → ``att.mtu_req`` / ``att.mtu_rsp``
  * ATT Handle Value Notification → ``att.notify_ota``  with handle
  * ATT Handle Value Indication   → ``att.indicate_ota`` with handle
  * Anything else                 → ``btle.other``      (just timestamped)

Procedures that need finer detail can re-parse the PCAP themselves with the
same scapy session — this fold is just to make timeline-level assertions
(cadence, ordering) cheap to write.
"""

from __future__ import annotations

import logging
from pathlib import Path

from bert.runner.timeline import EventSource, Timeline

log = logging.getLogger(__name__)


def fold_pcap_into_timeline(pcap_path: Path, timeline: Timeline) -> int:
    """Read ``pcap_path`` and append events to ``timeline``. Returns event count."""

    if not pcap_path.exists():
        log.warning("pcap file %s does not exist; skipping fold", pcap_path)
        return 0

    try:
        from scapy.all import PcapNgReader  # type: ignore[import-not-found]
        from scapy.layers.bluetooth4LE import (  # type: ignore[import-not-found]
            BTLE,
            BTLE_ADV,
            BTLE_DATA,
        )
    except Exception as exc:  # pragma: no cover  -- scapy missing or no BLE layer
        log.warning("scapy / bluetooth4LE not available, skipping PCAP fold: %s", exc)
        return 0

    count = 0
    try:
        reader = PcapNgReader(str(pcap_path))
    except Exception as exc:  # pragma: no cover
        log.error("could not open %s as PCAP-NG: %s", pcap_path, exc)
        return 0

    try:
        for pkt in reader:
            t_ns = int(float(pkt.time) * 1_000_000_000)
            # Accept either a top-level BTLE record or any encapsulating layer
            # exposed by scapy's BLE dissector.
            kind, data = _classify(pkt, BTLE, BTLE_ADV, BTLE_DATA)
            timeline.add(kind, EventSource.OTA, data=data, t_ns=t_ns)
            count += 1
    finally:
        reader.close()
    log.info("folded %d packets from %s into timeline", count, pcap_path)
    return count


def _classify(pkt: object, BTLE: type, BTLE_ADV: type, BTLE_DATA: type) -> tuple[str, dict]:
    """Map a scapy BLE packet to (kind, data) for timeline insertion."""

    # Lazily import scapy ATT dissectors only if a data PDU is present.
    if pkt.haslayer(BTLE_ADV):  # type: ignore[arg-type]
        adv = pkt.getlayer(BTLE_ADV)  # type: ignore[arg-type]
        return "btle.adv", {
            "adva": getattr(adv, "AdvA", None),
            "type": getattr(adv, "PDU_type", None),
        }
    if pkt.haslayer(BTLE_DATA):  # type: ignore[arg-type]
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
