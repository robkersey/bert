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
    """Yield ``(linktype, packet_bytes, t_ns)`` for every packet in a capture file.

    Auto-detects classic libpcap vs PCAP-NG. Timestamps are normalised to
    nanoseconds since the Unix epoch.

    Why both formats: ``nrfutil ble-sniffer`` writes classic libpcap (despite
    the ``.pcapng`` extension on disk), but PCAP-NG is the modern format
    other tools may produce. We accept both transparently.
    """

    with path.open("rb") as f:
        head = f.read(4)
        if len(head) < 4:
            return
        f.seek(0)

        # Classic libpcap magic numbers:
        #   0xa1b2c3d4 (LE-on-disk, μs)  → bytes "d4 c3 b2 a1"
        #   0xd4c3b2a1 (BE-on-disk, μs)  → bytes "a1 b2 c3 d4"
        #   0xa1b23c4d (LE-on-disk, ns)  → bytes "4d 3c b2 a1"
        #   0x4d3cb2a1 (BE-on-disk, ns)  → bytes "a1 b2 3c 4d"
        if head in (
            b"\xd4\xc3\xb2\xa1", b"\xa1\xb2\xc3\xd4",
            b"\x4d\x3c\xb2\xa1", b"\xa1\xb2\x3c\x4d",
        ):
            yield from _iter_classic_pcap(f)
            return

        # PCAP-NG starts with the Section Header Block: type 0x0A0D0D0A.
        if head == b"\x0a\x0d\x0d\x0a":
            yield from _iter_pcapng(f)
            return

        raise PcapNgParseError(
            f"unrecognised capture-file magic at start: {head.hex()}. "
            f"Expected libpcap (a1 b2 c3 d4 or byte-swapped) or PCAP-NG (0a 0d 0d 0a)."
        )


def _iter_classic_pcap(f) -> Iterator[tuple[int, bytes, int]]:
    """Read a classic libpcap (RFC 6201/Wireshark) file."""
    magic_bytes = f.read(4)
    # On-disk byte order: if the bytes spell out the magic in big-endian
    # ("a1 b2 c3 d4" or "a1 b2 3c 4d"), the file is big-endian; if reversed,
    # little-endian.
    if magic_bytes in (b"\xa1\xb2\xc3\xd4", b"\xa1\xb2\x3c\x4d"):
        order = ">"
    else:
        order = "<"
    is_nanosecond = magic_bytes in (b"\xa1\xb2\x3c\x4d", b"\x4d\x3c\xb2\xa1")
    # Rest of header: ver_major(2), ver_minor(2), thiszone(4), sigfigs(4),
    # snaplen(4), linktype(4) — total 20 more bytes after magic.
    rest = f.read(20)
    if len(rest) < 20:
        return
    _ver_major, _ver_minor, _thiszone, _sigfigs, _snaplen, linktype = struct.unpack(
        order + "HHiIII", rest
    )
    while True:
        rec_header = f.read(16)
        if len(rec_header) < 16:
            break
        ts_sec, ts_subsec, incl_len, _orig_len = struct.unpack(order + "IIII", rec_header)
        data = f.read(incl_len)
        if len(data) < incl_len:
            break
        if is_nanosecond:
            t_ns = ts_sec * 1_000_000_000 + ts_subsec
        else:
            t_ns = ts_sec * 1_000_000_000 + ts_subsec * 1000
        yield linktype, data, t_ns


def _iter_pcapng(f) -> Iterator[tuple[int, bytes, int]]:
    """Read a PCAP-NG file."""
    interfaces: list[tuple[int, int]] = []  # (linktype, ts_resol_units_per_second)
    section_byte_order = "<"
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
        f.read(4)

        if block_type == 0x0A0D0D0A:
            magic = struct.unpack(section_byte_order + "I", body[:4])[0]
            if magic == 0x4D3C2B1A:
                section_byte_order = ">"
            elif magic != 0x1A2B3C4D:
                raise PcapNgParseError(f"unrecognised SHB byte-order magic {magic:#x}")
            interfaces = []
        elif block_type == 0x00000001:
            if len(body) < 8:
                continue
            linktype, _reserved, _snaplen = struct.unpack(
                section_byte_order + "HHI", body[:8]
            )
            ts_resol = _parse_idb_options(body[8:], section_byte_order)
            interfaces.append((linktype, ts_resol))
        elif block_type == 0x00000006:
            if len(body) < 20:
                continue
            iface_id, ts_high, ts_low, cap_len, _orig_len = struct.unpack(
                section_byte_order + "IIIII", body[:20]
            )
            if iface_id >= len(interfaces):
                continue
            linktype, ts_resol = interfaces[iface_id]
            t_units = (ts_high << 32) | ts_low
            if ts_resol == 0:
                t_ns = t_units * 1000
            else:
                t_ns = (t_units * 1_000_000_000) // ts_resol
            yield linktype, bytes(body[20 : 20 + cap_len]), t_ns
        elif block_type == 0x00000003:
            continue


def parse_nordic_packet(raw: bytes) -> tuple[str, dict] | None:
    """Parse a single DLT_NORDIC_BLE packet body. Returns (kind, data) or None.

    The on-disk format produced by ``nrfutil ble-sniffer`` (firmware 4.x) is:

      bytes 0-6   : nrfutil firmware UART preamble (7 bytes; sync/length/board)
      bytes 7+    : Nordic per-packet header — first byte = header length
                    (typically 10): flags, channel, rssi, event_counter (2),
                    delta_time (4)
      then        : the BLE LL packet (access address, LL header, payload)

    We auto-detect the Nordic header offset by looking for a plausible
    header_length field (typically 0x0a = 10, but some firmware variants
    differ). Falls through to the legacy "header at offset 0" interpretation
    for compatibility with older captures.
    """

    if len(raw) < 6:
        return None

    # Try the nrfutil-ble-sniffer 4.x layout first: 7-byte preamble, then
    # Nordic header. The first byte of the Nordic header is its own length.
    nordic_offset = _find_nordic_header_offset(raw)
    if nordic_offset is None:
        return None

    header_length = raw[nordic_offset]
    if (
        header_length < 5
        or nordic_offset + header_length > len(raw)
    ):
        return None

    flags = raw[nordic_offset + 1]
    channel = raw[nordic_offset + 2]
    # Nordic stores RSSI as the absolute value (0..127) representing -dBm.
    rssi_raw = raw[nordic_offset + 3]
    rssi = -rssi_raw if rssi_raw <= 127 else rssi_raw - 256
    direction = "central->peripheral" if (flags & 0x01) else "peripheral->central"
    ble = raw[nordic_offset + header_length :]
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


def _find_nordic_header_offset(raw: bytes) -> int | None:
    """Locate the Nordic per-packet header within a PCAP record.

    Returns the byte offset where the Nordic header starts, or None if the
    packet doesn't look like a recognisable nRF Sniffer record.

    Heuristic: a valid header_length is 10 in current firmware (older
    variants used 7 or 11). At candidate offsets {7, 0}, check that:
      * byte[off]      is in {7, 10, 11}
      * byte[off + 2]  (channel)  is in 0..39
      * byte[off + 1]  (flags)    is in 0..0xff (always true; sanity)

    Order: try 7 first (nrfutil ble-sniffer 4.x — what we actually see), fall
    back to 0 (older raw-Nordic-header captures).
    """
    valid_header_lengths = (10, 11, 7)
    for off in (7, 0):
        if off + 4 > len(raw):
            continue
        hdr_len = raw[off]
        if hdr_len not in valid_header_lengths:
            continue
        if raw[off + 2] > 39:  # channel out of range
            continue
        if off + hdr_len + 6 > len(raw):  # need at least access_addr + LL header
            continue
        return off
    return None


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
