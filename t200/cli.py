"""Command-line interface: ``python -m t200 ...``."""

from __future__ import annotations

import argparse
import sys
import time

from . import protocol as p
from .spp import Buds, available_ports, find_port


def _hex_bytes(value: str) -> bytes:
    cleaned = value.replace(" ", "").replace("-", "").replace("0x", "")
    if not cleaned:
        return b""
    if len(cleaned) % 2:
        raise argparse.ArgumentTypeError("hex payload has an odd number of digits")
    try:
        return bytes.fromhex(cleaned)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid hex payload: {value!r}") from exc


def _cmd_number(value: str) -> int:
    try:
        return int(value, 16)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid command {value!r}; use hex, e.g. 0x0106"
        ) from exc


def _seq_number(value: str) -> int:
    try:
        number = int(value, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid sequence byte: {value!r}") from exc
    if not 0 <= number <= 0xFF:
        raise argparse.ArgumentTypeError("sequence byte must be 0..255")
    return number


def _load_hex(value: str) -> bytes:
    if value.startswith("@"):
        from pathlib import Path
        text = Path(value[1:]).read_text(encoding="utf-8")
        return _hex_bytes(text)
    return _hex_bytes(value)


def _print_frames(frames: list[p.Frame]) -> None:
    if not frames:
        print("  (no valid reply)")
        return

    for frame in frames:
        print(f"  {frame.summary()}  {frame.data.hex() or '-'}")

        if frame.base_cmd in (p.CMD_BATTERY, p.CMD_BATTERY_PUSH):
            levels = p.parse_battery(frame.data)
            if levels:
                print(f"       {p.format_battery(levels)}")

        elif frame.base_cmd == p.CMD_FIRMWARE:
            versions = p.parse_firmware(frame.data)
            if versions:
                print(f"       {', '.join(versions)}")

        elif frame.base_cmd == p.CMD_ANC_STATUS:
            mode = p.parse_anc_mode(frame.data)
            if mode is not None:
                print(f"       mode=0x{mode:02x} ({p.ANC_MODES.get(mode, 'unknown')})")

        elif frame.base_cmd == p.CMD_ANC_SET:
            status = frame.data[0] if frame.data else None
            if status is not None:
                print(f"       status=0x{status:02x} "
                      f"({'ok' if status == 0 else 'rejected'})")

        elif frame.base_cmd == p.CMD_GESTURES:
            rows = p.parse_gestures(frame.data)
            if rows:
                for line in p.format_gestures(rows).splitlines():
                    print(f"       {line}")

        elif frame.base_cmd == p.CMD_DEVICE_INFO:
            text = p.parse_ascii(frame.data, skip=1)
            if text:
                print(f"       {text!r}")


def _open(args) -> Buds:
    port = find_port(mac=args.mac, port=args.port)
    print(f"[*] SPP port: {port}")
    buds = Buds(port, timeout=args.timeout)
    buds.open()
    return buds


def _send(buds: Buds, frame: bytes, quiet: bool = False) -> list[p.Frame]:
    if not quiet:
        print(f"[>] {frame.hex()}")
    frames = buds.transact(frame, timeout=buds.timeout)
    _print_frames(frames)
    return frames


def cmd_ports(_args) -> int:
    ports = available_ports()
    if not ports:
        print("No serial ports found.")
        return 1
    for port in ports:
        text = f"{port.hwid} {port.description}".upper()
        kind = "BT" if ("BTHENUM" in text or "BLUETOOTH" in text) else "  "
        mac = port.mac or ""
        print(f"{kind}  {port.device:<8} {port.description:<42} {mac}")
    return 0


def cmd_anc(args) -> int:
    buds = _open(args)
    try:
        print(f"[*] ANC mode: {args.mode}")
        _send(buds, p.anc_frame(args.mode, seq=args.seq))
        return 0
    finally:
        buds.close()


def cmd_status(args) -> int:
    buds = _open(args)
    try:
        seq = args.seq
        print("[*] ANC status")
        _send(buds, p.anc_status_frame(seq))
        print("[*] Battery")
        _send(buds, p.battery_frame((seq + 1) & 0xFF))
        print("[*] Firmware")
        _send(buds, p.firmware_frame((seq + 2) & 0xFF))
        return 0
    finally:
        buds.close()


def cmd_battery(args) -> int:
    buds = _open(args)
    try:
        _send(buds, p.battery_frame(seq=args.seq))
        return 0
    finally:
        buds.close()


def cmd_firmware(args) -> int:
    buds = _open(args)
    try:
        _send(buds, p.firmware_frame(seq=args.seq))
        return 0
    finally:
        buds.close()


def cmd_gestures(args) -> int:
    buds = _open(args)
    try:
        _send(buds, p.gestures_frame(seq=args.seq))
        return 0
    finally:
        buds.close()


def cmd_query(args) -> int:
    buds = _open(args)
    try:
        frame = p.encode(args.cmd, args.data, seq=args.seq)
        _send(buds, frame)
        return 0
    finally:
        buds.close()


def cmd_raw(args) -> int:
    buds = _open(args)
    try:
        print("[!] raw frames can change device settings")
        _send(buds, args.frame)
        return 0
    finally:
        buds.close()


def cmd_decode(args) -> int:
    buf = _load_hex(args.hex)
    frames, rest = p.decode_stream(buf)
    print(f"[*] {len(buf)} bytes, {len(frames)} frame(s), {len(rest)} trailing byte(s)")
    _print_frames(frames)
    if rest:
        print(f"[!] unparsed remainder: {rest.hex()}")
    return 0 if frames else 1


def cmd_monitor(args) -> int:
    buds = _open(args)
    print(f"[*] listening for pushes for {args.seconds:.0f}s (Ctrl+C to stop)")
    deadline = time.monotonic() + args.seconds
    try:
        while time.monotonic() < deadline:
            frames = buds.read(timeout=1.0)
            if frames:
                _print_frames(frames)
    except KeyboardInterrupt:
        print("\n[*] stopped")
    finally:
        buds.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--port", help="serial port, e.g. COM4 or /dev/rfcomm0")
    common.add_argument("--mac", help="buds Bluetooth MAC (auto-detect the port)")
    common.add_argument("--timeout", type=float, default=2.0,
                        help="reply timeout in seconds (default: 2.0)")
    common.add_argument("--seq", type=_seq_number, default=0x10,
                        help="sequence byte (default: 0x10)")

    parser = argparse.ArgumentParser(
        prog="python -m t200",
        description="Local, account-free control of realme Buds T200.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("ports", help="list Bluetooth serial ports")
    sub.choices["ports"].set_defaults(func=cmd_ports)

    anc = sub.add_parser("anc", parents=[common],
                         help="set ANC / transparency / off")
    anc.add_argument("mode", choices=sorted(p.ANC_SET_VALUES))
    anc.set_defaults(func=cmd_anc)

    status = sub.add_parser("status", parents=[common],
                            help="read ANC, battery, and firmware")
    status.set_defaults(func=cmd_status)

    for name, func, help_text in (
        ("battery", cmd_battery, "read left/right/case battery"),
        ("firmware", cmd_firmware, "read firmware version strings"),
        ("gestures", cmd_gestures, "read the touch-gesture map"),
    ):
        item = sub.add_parser(name, parents=[common], help=help_text)
        item.set_defaults(func=func)

    query = sub.add_parser("query", parents=[common],
                           help="send a read command by number")
    query.add_argument("cmd", type=_cmd_number,
                       help="command in hex, e.g. 0x0106")
    query.add_argument("--data", type=_hex_bytes, default=b"",
                       help="optional hex payload")
    query.set_defaults(func=cmd_query)

    raw = sub.add_parser("raw", parents=[common], help="send an arbitrary frame")
    raw.add_argument("frame", type=_hex_bytes, help="full frame in hex")
    raw.set_defaults(func=cmd_raw)

    decode = sub.add_parser("decode", help="decode hex frames offline")
    decode.add_argument("hex", help="hex bytes, or @file.hex")
    decode.set_defaults(func=cmd_decode)

    monitor = sub.add_parser("monitor", parents=[common],
                             help="print unsolicited pushes")
    monitor.add_argument("--seconds", type=float, default=30.0)
    monitor.set_defaults(func=cmd_monitor)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except RuntimeError as exc:
        print(f"[!] {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(f"[!] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
