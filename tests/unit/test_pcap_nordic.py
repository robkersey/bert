"""DLT_NORDIC_BLE parser + PCAP-NG reader."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from bert.adapters import pcap_nordic


# --------------------------------------------------------------------------- #
# parse_nordic_packet                                                          #
# --------------------------------------------------------------------------- #


def _nordic_header(channel: int = 37, rssi: int = -40, header_length: int = 10) -> bytes:
    """Build a synthetic Nordic packet record (nrfutil ble-sniffer 4.x layout):

    7-byte UART preamble (just zeroed-out; we don't actually parse it),
    followed by the Nordic per-packet header where the first byte IS the
    header length.

    Nordic stores RSSI as absolute value (0..127 representing -dBm), so we
    write the absolute value here.
    """
    rssi_abs = abs(rssi) & 0xFF
    uart_preamble = bytes(7)
    nordic_header = bytes([
        header_length,      # header length
        0x00,               # flags
        channel,            # channel
        rssi_abs,           # rssi (positive == -dBm)
    ]) + bytes(header_length - 4)  # event counter + delta time
    return uart_preamble + nordic_header


def _adv_ind_packet(adva: bytes = b"\x06\x05\x04\x03\x02\x01") -> bytes:
    """ADV_IND advertising packet on the SIG advertising channel.

    Layout: access_address(4) + LL header(2) + AdvA(6) + AdvData(...).
    """
    access = struct.pack("<I", pcap_nordic.ADVERTISING_ACCESS_ADDRESS)
    ll_header = bytes([0x00, 0x09])  # PDU type 0 = ADV_IND, length 9
    payload = adva  # AdvA is the first 6 bytes of payload
    return access + ll_header + payload


def test_parse_nordic_adv_ind_packet() -> None:
    raw = _nordic_header(channel=37) + _adv_ind_packet(b"\xAA\xBB\xCC\xDD\xEE\xFF")
    out = pcap_nordic.parse_nordic_packet(raw)
    assert out is not None
    kind, data = out
    assert kind == "btle.adv"
    assert data["pdu"] == "ADV_IND"
    assert data["channel"] == 37
    assert data["rssi"] == -40
    # AdvA on the wire is little-endian; we display big-endian (most-significant
    # byte first), so 0xAA..0xFF on the wire renders as FF:EE:DD:CC:BB:AA.
    assert data["adva"] == "FF:EE:DD:CC:BB:AA"


def test_parse_nordic_data_pdu() -> None:
    raw = (
        _nordic_header(channel=10, rssi=-55)
        + struct.pack("<I", 0x12345678)  # data-channel access address (not the SIG one)
        + bytes([0x02, 0x05])  # LL data PDU header
        + b"\x00" * 5
    )
    out = pcap_nordic.parse_nordic_packet(raw)
    assert out is not None
    assert out[0] == "btle.data"
    assert out[1]["channel"] == 10
    assert out[1]["rssi"] == -55
    assert out[1]["access_address"] == "0x12345678"


def test_parse_nordic_too_short() -> None:
    assert pcap_nordic.parse_nordic_packet(b"") is None
    assert pcap_nordic.parse_nordic_packet(b"\x00\x00") is None


def test_parse_nordic_invalid_header_length() -> None:
    raw = bytes([0, 99, 0, 37, 200])  # header_length way bigger than buffer
    assert pcap_nordic.parse_nordic_packet(raw) is None


# --------------------------------------------------------------------------- #
# iter_packets                                                                  #
# --------------------------------------------------------------------------- #


def _build_pcapng(linktype: int, packets: list[tuple[bytes, int]]) -> bytes:
    """Build a minimal PCAP-NG file: SHB + IDB + N EPBs.

    ``packets`` is a list of (raw_bytes, t_microseconds_since_epoch).
    """
    # Section Header Block: type=0x0A0D0D0A, length, byte_order_magic, ver, sect_len, length
    shb_body = struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1)
    shb = struct.pack("<II", 0x0A0D0D0A, 12 + len(shb_body)) + shb_body + struct.pack("<I", 12 + len(shb_body))

    # Interface Description Block: linktype (2), reserved (2), snaplen (4)
    idb_body = struct.pack("<HHI", linktype, 0, 0xFFFF)
    idb = struct.pack("<II", 0x00000001, 12 + len(idb_body)) + idb_body + struct.pack("<I", 12 + len(idb_body))

    blocks = [shb, idb]
    for raw, t_us in packets:
        # Enhanced Packet Block: iface_id, ts_high, ts_low, cap_len, orig_len, packet
        ts_high = (t_us >> 32) & 0xFFFFFFFF
        ts_low = t_us & 0xFFFFFFFF
        epb_body = struct.pack("<IIIII", 0, ts_high, ts_low, len(raw), len(raw)) + raw
        # Pad packet data to 4-byte boundary
        pad = (-len(epb_body)) & 3
        epb_body += b"\x00" * pad
        epb = struct.pack("<II", 0x00000006, 12 + len(epb_body)) + epb_body + struct.pack("<I", 12 + len(epb_body))
        blocks.append(epb)
    return b"".join(blocks)


def test_iter_packets_extracts_nordic_advs(tmp_path: Path) -> None:
    raw_packet = _nordic_header() + _adv_ind_packet()
    pcap = tmp_path / "fake.pcapng"
    pcap.write_bytes(_build_pcapng(pcap_nordic.DLT_NORDIC_BLE, [
        (raw_packet, 1_000_000),  # 1 second
        (raw_packet, 1_030_000),  # 1.03 seconds (30ms later)
    ]))
    seen = list(pcap_nordic.iter_packets(pcap))
    assert len(seen) == 2
    linktype, raw, t_ns = seen[0]
    assert linktype == pcap_nordic.DLT_NORDIC_BLE
    assert t_ns == 1_000_000_000  # 1s in ns
    # Packet bytes round-tripped intact
    assert raw == raw_packet


def test_iter_packets_handles_empty_file(tmp_path: Path) -> None:
    pcap = tmp_path / "empty.pcapng"
    pcap.write_bytes(b"")
    assert list(pcap_nordic.iter_packets(pcap)) == []


def test_iter_packets_rejects_invalid_byte_order_magic(tmp_path: Path) -> None:
    pcap = tmp_path / "bad.pcapng"
    # Build an SHB with a bogus byte-order magic.
    body = struct.pack("<IHHq", 0xDEADBEEF, 1, 0, -1)
    raw = struct.pack("<II", 0x0A0D0D0A, 12 + len(body)) + body + struct.pack("<I", 12 + len(body))
    pcap.write_bytes(raw)
    with pytest.raises(pcap_nordic.PcapNgParseError):
        list(pcap_nordic.iter_packets(pcap))


# --------------------------------------------------------------------------- #
# End-to-end fold                                                               #
# --------------------------------------------------------------------------- #


def test_fold_emits_adv_events_with_correct_timing(tmp_path: Path) -> None:
    """The fold should produce a btle.adv event per advertising packet, with
    timestamps spaced as the PCAP records them."""
    from bert.adapters.pcap import fold_pcap_into_timeline
    from bert.runner.timeline import EventSource, Timeline

    pcap = tmp_path / "advs.pcapng"
    raw = _nordic_header() + _adv_ind_packet(b"\xFE\xDC\xBA\x98\x76\x54")
    # Five advertisements 30ms apart = 33Hz.
    pcap.write_bytes(_build_pcapng(
        pcap_nordic.DLT_NORDIC_BLE,
        [(raw, 30_000 * i) for i in range(5)],
    ))
    timeline = Timeline()
    n = fold_pcap_into_timeline(pcap, timeline)
    assert n == 5
    advs = timeline.select(kind="btle.adv", source=EventSource.OTA)
    assert len(advs) == 5
    # Inter-arrival should be ~30ms.
    deltas = timeline.deltas_ms(advs)
    assert all(abs(d - 30.0) < 0.01 for d in deltas)
    assert advs[0].data["adva"] == "54:76:98:BA:DC:FE"
