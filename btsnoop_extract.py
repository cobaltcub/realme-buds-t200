#!/usr/bin/env python3
"""Decode realme/OPPO ``aa`` frames from an Android btsnoop HCI log.

Android's "Bluetooth HCI snoop log" developer option records every HCI packet.
This tool rebuilds the L2CAP/RFCOMM stream, extracts UIH payloads, and decodes
any complete aa frames with :mod:`t200.protocol`.

Usage::

    python btsnoop_extract.py btsnoop_hci.log
    python btsnoop_extract.py btsnoop_hci.log --mac AA:BB:CC:DD:EE:FF
    python btsnoop_extract.py btsnoop_hci.log --show-raw

Find the log with::

    adb shell bugreportz -p
    adb pull /data/user_de/0/com.android.shell/files/bugreports/<file>.zip .
    # inside: FS/data/misc/bluetooth/logs/btsnoop_hci.log
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

from t200 import protocol as p
from t200.spp import normalize_mac

BTSNOOP_MAGIC = b"btsnoop\x00"
DATALINK_H4 = 1002

HCI_ACL = 0x02
HCI_EVENT = 0x04

FLAG_RECEIVED = 0x01


def read_records(path: Path):
    raw = path.read_bytes()
    if not raw.startswith(BTSNOOP_MAGIC):
        raise ValueError(f"{path} is not a btsnoop file")

    version, datalink = struct.unpack_from(">II", raw, 8)
    if datalink != DATALINK_H4:
        raise ValueError(f"unsupported btsnoop datalink {datalink} (need H4/1002)")
    if version != 1:
        print(f"[!] unexpected btsnoop version {version}, trying anyway")

    offset = 16
    while offset + 24 <= len(raw):
        _orig_len, incl_len, flags, _drops = struct.unpack_from(">IIII", raw, offset)
        timestamp = struct.unpack_from(">Q", raw, offset + 16)[0]
        offset += 24
        packet = raw[offset:offset + incl_len]
        offset += incl_len
        yield timestamp, flags, packet


def collect_addresses(path: Path) -> dict[int, str]:
    """Map ACL connection handles to Bluetooth addresses from HCI events."""
    handles: dict[int, str] = {}
    for _ts, _flags, packet in read_records(path):
        if len(packet) < 3 or packet[0] != HCI_EVENT:
            continue
        event = packet[1]
        body = packet[3:]

        # HCI Connection Complete: status, handle, bdaddr (little-endian)
        if event == 0x03 and len(body) >= 9:
            status = body[0]
            handle = struct.unpack_from("<H", body, 1)[0] & 0x0FFF
            address = body[3:9]
            if status == 0:
                handles[handle] = ":".join(f"{b:02X}" for b in reversed(address))

        # LE Meta Event / LE Connection Complete
        elif event == 0x3E and len(body) >= 12 and body[0] == 0x01:
            status = body[1]
            handle = struct.unpack_from("<H", body, 2)[0] & 0x0FFF
            address = body[6:12]
            if status == 0:
                handles.setdefault(
                    handle,
                    ":".join(f"{b:02X}" for b in reversed(address)),
                )

    return handles


class L2CAPReassembler:
    """Rebuild L2CAP PDUs from (possibly fragmented) ACL payloads."""

    def __init__(self) -> None:
        self._buffers: dict[int, bytearray] = {}

    def feed(self, handle: int, pb: int, payload: bytes):
        if pb == 0b01:  # continuation fragment
            buf = self._buffers.setdefault(handle, bytearray())
            buf.extend(payload)
        else:  # first/complete fragment
            buf = self._buffers[handle] = bytearray(payload)

        while len(buf) >= 4:
            l2cap_len = struct.unpack_from("<H", buf, 0)[0]
            cid = struct.unpack_from("<H", buf, 2)[0]
            if l2cap_len == 0 and cid == 0:
                del buf[:4]
                continue
            if len(buf) < 4 + l2cap_len:
                return
            pdu = bytes(buf[4:4 + l2cap_len])
            del buf[:4 + l2cap_len]
            yield cid, pdu


def rfcomm_payloads(pdu: bytes):
    """Yield ``(dlci, data)`` for UIH frames inside an RFCOMM PDU."""
    if len(pdu) < 3:
        return
    address, control = pdu[0], pdu[1]
    if (control & 0xEF) != 0xEF:  # not a UIH frame
        return

    dlci = (address >> 2) & 0x3F
    length_byte = pdu[2]
    if length_byte & 0x01:  # EA=1, one-byte length
        offset = 3
        length = length_byte >> 1
    else:  # EA=0, two-byte length
        if len(pdu) < 4:
            return
        offset = 4
        length = ((pdu[3] << 7) | (length_byte >> 1))

    # Credit-based flow control adds one credit byte after the length. We do
    # not track PN negotiation, so try both offsets and keep candidates that
    # actually contain an aa frame.
    seen = set()
    for extra in (0, 1):
        start = offset + extra
        data = pdu[start:start + length]
        if len(data) != length or b"\xaa" not in data:
            continue
        if data not in seen:
            seen.add(data)
            yield dlci, data


def describe(frame: p.Frame) -> list[str]:
    lines = [frame.summary()]
    if frame.base_cmd in (p.CMD_BATTERY, p.CMD_BATTERY_PUSH):
        levels = p.parse_battery(frame.data)
        if levels:
            lines.append(p.format_battery(levels))
    elif frame.base_cmd == p.CMD_FIRMWARE:
        versions = p.parse_firmware(frame.data)
        if versions:
            lines.append(", ".join(versions))
    elif frame.base_cmd == p.CMD_ANC_STATUS:
        mode = p.parse_anc_mode(frame.data)
        if mode is not None:
            lines.append(f"mode=0x{mode:02x} ({p.ANC_MODES.get(mode, 'unknown')})")
    elif frame.base_cmd == p.CMD_GESTURES:
        rows = p.parse_gestures(frame.data)
        for side, gesture_type, action in rows:
            side_name = p.SIDE_NAMES.get(side, f"side{side}")
            type_name = p.GESTURE_TYPES.get(gesture_type, f"0x{gesture_type:04x}")
            lines.append(f"{side_name:5} {type_name:7} action={action}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract and decode Realme/OPPO aa frames from a btsnoop log."
    )
    parser.add_argument("log", type=Path)
    parser.add_argument("--mac", help="only frames for this Bluetooth address")
    parser.add_argument("--show-raw", action="store_true",
                        help="also print RFCOMM payloads with no valid aa frame")
    args = parser.parse_args(argv)

    target = normalize_mac(args.mac) if args.mac else None
    if not args.log.exists():
        print(f"[!] file not found: {args.log}")
        return 1
    try:
        handles = collect_addresses(args.log)
    except (OSError, ValueError) as exc:
        print(f"[!] {exc}")
        return 1
    if target and not handles:
        print("[!] no connection events found; MAC filter unavailable")

    if handles:
        for handle, address in sorted(handles.items()):
            mark = "  <- target" if target and address == target else ""
            print(f"[*] handle 0x{handle:04x} -> {address}{mark}")

    target_handles = {
        handle for handle, address in handles.items()
        if target is None or address == target
    } if target else set(handles)

    reassembler = L2CAPReassembler()
    first_ts = None
    frames_seen = 0
    raw_seen = 0

    for timestamp, flags, packet in read_records(args.log):
        if not packet or packet[0] != HCI_ACL or len(packet) < 5:
            continue
        if first_ts is None:
            first_ts = timestamp

        handle_flags = struct.unpack_from("<H", packet, 1)[0]
        handle = handle_flags & 0x0FFF
        pb = (handle_flags >> 12) & 0x03
        acl_len = struct.unpack_from("<H", packet, 3)[0]
        payload = packet[5:5 + acl_len]

        if target and handles and handle not in target_handles:
            continue

        received = bool(flags & FLAG_RECEIVED)
        direction = "buds -> host" if received else "host -> buds"
        delta = (timestamp - first_ts) / 1_000_000

        for cid, pdu in reassembler.feed(handle, pb, payload):
            if cid < 0x0040:
                continue
            for dlci, rfcomm in rfcomm_payloads(pdu):
                frames, _rest = p.decode_stream(rfcomm)
                if not frames:
                    raw_seen += 1
                    if args.show_raw:
                        print(f"[{delta:8.3f}s] {direction}  dlci={dlci}  "
                              f"raw={rfcomm.hex()}")
                    continue
                for frame in frames:
                    frames_seen += 1
                    lines = describe(frame)
                    print(f"[{delta:8.3f}s] {direction}  dlci={dlci}  "
                          f"{lines[0]}  {frame.data.hex() or '-'}")
                    for line in lines[1:]:
                        print(f"{'':12} {line}")

    print(f"[*] decoded {frames_seen} aa frame(s)"
          + (f", skipped {raw_seen} non-aa RFCOMM payload(s)" if raw_seen else ""))
    return 0 if frames_seen else 1


if __name__ == "__main__":
    raise SystemExit(main())
