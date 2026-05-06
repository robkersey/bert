"""Parse PCAP-NG files written by ``nrfutil ble-sniffer``.

Nordic's nRF Sniffer for Bluetooth LE writes captures with the Wireshark
DLT 272 (``DLT_NORDIC_BLE``). Scapy doesn't recognise this DLT and falls
back to ``Raw``, which is why our previous fold-into-timeline implementation
returned 0 packets against real captures.

This module is a pure-Python PCAP-NG reader plus a parser for the Nordic
per-packet header. We extract just enough metadata to satisfy
timeline-level assertions (advertising cadence, MTU exchange ordering):

  * advertising packets   →  kind=``btle.adv``  with adva, pdu_type, channel, rssi
  * data PDUs             →  kind=``btle.data``  with channel, rssi, access_address
  * standard SIG DLTs     →  delegated to scapy.layers.bluetooth4LE if available

The Nordic packet layout (per their Wireshark dissector
``epan/dissectors/packet-nordic_ble.c``):

  [0]  board_id
  [1]  header_length        (typically 10 or 11 — use this to skip the header)
  [2]  flags
  [3]  channel              (0..39; 37/38/39 are the LE advertising channels)
  [4]  rssi                 (signed 8-bit, dBm)
  [5..7]  event_counter LE  (some firmware variants only)
  [N..N+4]  delta_time LE   (microseconds since previous packet)

After the Nordic header comes the BLE LL packet:

  [0..4]  access_address (LE)
  [4..6]  LL header (PDU type in low 4 bits of byte 0; length in byte 1)
  [6..]   payload (AdvA at offset 6..12 for ADV_IND-family packets)
"""

from __future__ import annotations

import logging
import struct
from collections.abc import Iterator
from pathlib import Path

log = logging.getLogger(__name__)


# DLT (Wireshark Data Link Type) constants.
DLT_NORDIC_BLE = 272
DLT_BLUETOOTH_LE_LL = 251
DLT_BLUETOOTH_LE_LL_WITH_PHDR = 256

# The SIG-defined access address for advertising channels (37/38/39).
ADVERTISING_ACCESS_ADDRESS = 0x8E89BED6

# Advertising-type PDUs we know how to extract AdvA from.
ADV_PDU_TYPES = {
    0x0: "ADV_IND",
    0x1: "ADV_DIRECT_IND",
    0x2: "ADV_NONCONN_IND",
    0x4: "SCAN_RSP",
    0x6: "ADV_SCAN_IND",
    0x7: "ADV_EXT_IND",
}


class PcapNgParseError(RuntimeError):
    """A PCAP-NG file we couldn't decode."""


# --------------------------------------------------------------------------- #
# Public API                                                                   #
# --------------------------------------------------------------------------- #


def iter_packets(path: Path) -> Iterator[tuple[int, bytes, int]]:
    """Yield ``(linktype, packet_bytes, t_ns)`` for every packet in a PCAP-NG file.

    Timestamps are normalised to nanoseconds since the Unix epoch. The default
    PCAP-NG resolution is microseconds; we honour the ``if_tsresol`` option on
    each Interface Description Block when present.
    """

    with path.open("rb") as f:
        interfaces: list[tuple[int, int]] = []  # (linktype, ts_resol_units_per_second)
        section_byte_order = "<"  # little-endian by default
        while True:
            header = f.read(8)
            if len(header) < 8:
                break
            block_type, block_total_length = struct.unpack(section_byte_order + "II", header)
            if block_total_length < 12:
                raise PcapNgParseError(
                    f"block at offset {f.tell() - 8} has length {block_total_length} < 12"
                )
            body = f.read(block_total_length - 12)
            f.read(4)  # trailing block_total_length, ignored

            if block_type == 0x0A0D0D0A:  # Section Header Block
                magic = struct.unpack(section_byte_order + "I", body[:4])[0]
                if magic == 0x4D3C2B1A:
                    # Big-endian section.
                    section_byte_order = ">"
                elif magic != 0x1A2B3C4D:
                    raise PcapNgParseError(f"unrecognised SHB byte-order magic {magic:#x}")
                # Reset interfaces — each section has its own list.
                interfaces = []
            elif block_type == 0x00000001:  # Interface Description Block
                if len(body) < 8:
                    continue
                linktype, _reserved, _snaplen = struct.unpack(
                    section_byte_order + "HHI", body[:8]
                )
                ts_resol = _parse_idb_options(body[8:], section_byte_order)
                interfaces.append((linktype, ts_resol))
            elif block_type == 0x00000006:  # Enhanced Packet Block
                if len(body) < 20:
                    continue
                iface_id, ts_high, ts_low, cap_len, _orig_len = struct.unpack(
                    section_byte_order + "IIIII", body[:20]
                )
                if iface_id >= len(interfaces):
                    continue
                linktype, ts_resol = interfaces[iface_id]
                t_units = (ts_high << 32) | ts_low
                # Convert to ns regardless of resolution.
                if ts_resol == 0:  # 0 means 10^-6 (microseconds), the default
                    t_ns = t_units * 1000
                else:
                    t_ns = (t_units * 1_000_000_000) // ts_resol
                yield linktype, bytes(body[20 : 20 + cap_len]), t_ns
            elif block_type == 0x00000003:  # Simple Packet Block (rare)
                continue
            # All other block types ignored.


def parse_nordic_packet(raw: bytes) -> tuple[str, dict] | None:
    """Parse a single DLT_NORDIC_BLE packet body. Returns (kind, data) or None."""

    if len(raw) < 6:
        return None
    header_length = raw[1]
    if header_length < 5 or header_length > len(raw):
        return None
    flags = raw[2]
    channel = raw[3]
    rssi_raw = raw[4]
    rssi = rssi_raw - 256 if rssi_raw > 127 else rssi_raw
    direction = "central->peripheral" if (flags & 0x01) else "peripheral->central"
    ble = raw[header_length:]
    if len(ble) < 6:
        return "btle.unknown", {"channel": channel, "rssi": rssi}

    access_address = int.from_bytes(ble[0:4], "little")
    pdu_type = ble[4] & 0x0F

    is_advertising = (
        access_address == ADVERTISING_ACCESS_ADDRESS or 37 <= channel <= 39
    )
    if is_advertising:
        if pdu_type in ADV_PDU_TYPES:
            data: dict[str, object] = {
                "pdu": ADV_PDU_TYPES[pdu_type],
                "pdu_type": pdu_type,
                "channel": channel,
                "rssi": rssi,
                "direction": direction,
            }
            # AdvA is at bytes 6..12 of the BLE LL packet for these PDU types
            # (after the 4-byte access address and 2-byte LL header).
            if len(ble) >= 12:
                # BLE BD addresses are little-endian on the wire.
                adv_a = ble[6:12][::-1].hex(":").upper()
                data["adva"] = adv_a
            return "btle.adv", data
        return "btle.adv_other", {
            "pdu_type": pdu_type, "channel": channel, "rssi": rssi
        }

    # Data-channel packet (LL_DATA_PDU). We could split LLID etc. but the
    # timeline doesn't yet need that detail.
    return "btle.data", {
        "channel": channel,
        "rssi": rssi,
        "access_address": f"0x{access_address:08X}",
        "direction": direction,
    }


# --------------------------------------------------------------------------- #
# Internals                                                                    #
# --------------------------------------------------------------------------- #


def _parse_idb_options(opts: bytes, byte_order: str) -> int:
    """Walk an Interface Description Block's options and return the
    timestamp-resolution multiplier (units per second). Returns 0 (= default
    10^-6 / microseconds) when the option is absent.
    """

    i = 0
    while i + 4 <= len(opts):
        opt_code, opt_len = struct.unpack(byte_order + "HH", opts[i : i + 4])
        i += 4
        opt_value = opts[i : i + opt_len]
        # Pad to 4-byte boundary.
        i += (opt_len + 3) & ~3
        if opt_code == 0:  # opt_endofopt
            break
        if opt_code == 9 and len(opt_value) >= 1:  # if_tsresol
            byte = opt_value[0]
            unit = byte & 0x7F
            if byte & 0x80:
                # 2 ** unit
                return 1 << unit
            else:
                # 10 ** unit
                resol = 1
                for _ in range(unit):
                    resol *= 10
                return resol
    return 0
