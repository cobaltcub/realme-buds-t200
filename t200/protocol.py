"""Codec for the OPPO/realme ``aa`` control protocol used by the Buds T200.

Frame layout (verified against live T200 traffic)::

    AA | LEN | FLAG | 00 | CAT | SUB | SEQ | DLEN (u16le) | DATA

* ``LEN`` is a LEB128 varint equal to ``7 + len(DATA)``. It counts every
  byte after the varint itself, so a frame is ``1 + len(varint) + LEN`` bytes.
  Small frames need one byte; large replies (system dumps) use two and desync
  parsers that always assume ``LEN + 2``.
* ``FLAG`` is ``0x00`` for commands/replies and ``0x08`` for unsolicited pushes.
* The command number is ``CAT | (SUB << 8)``. A reply sets bit 7 of ``SUB``,
  so ``0x0106`` (battery query) is answered with ``0x8106``.
* ``DLEN`` is the payload length in bytes, little-endian.

The buds answer immediately after pairing; there is no login or handshake.
Sequence numbers are chosen by the host and echoed by the buds.
"""

from __future__ import annotations

from dataclasses import dataclass

SOF = 0xAA
FLAG_COMMAND = 0x00
FLAG_PUSH = 0x08

#: Bytes between the length varint and the payload: flag, pad, cat, sub, seq, dlen.
_FIXED_HEADER = 7
_MAX_VARINT_BYTES = 4

# Commands verified on a realme Buds T200.
CMD_DEVICE_INFO = 0x0104
CMD_FIRMWARE = 0x0105
CMD_BATTERY = 0x0106
CMD_GESTURES = 0x0108
CMD_ANC_STATUS = 0x010C
CMD_BATTERY_PUSH = 0x0204
CMD_SETTINGS = 0x0401
CMD_ANC_SET = 0x0404
CMD_HEARTBEAT = 0x050E

COMMAND_NAMES = {
    CMD_DEVICE_INFO: "device info",
    CMD_FIRMWARE: "firmware",
    CMD_BATTERY: "battery",
    CMD_GESTURES: "gesture map",
    CMD_ANC_STATUS: "ANC status",
    CMD_BATTERY_PUSH: "battery push",
    CMD_SETTINGS: "settings table",
    CMD_ANC_SET: "ANC set",
    CMD_HEARTBEAT: "heartbeat",
}

#: ANC mode byte. 0x01, 0x02 and 0x08 are verified on the T200; the other
#: values are used by sibling BBK models and are listed for completeness.
ANC_MODES = {
    0x01: "off",
    0x02: "transparent",
    0x04: "ANC (mild)",
    0x08: "ANC",
    0x10: "ANC (moderate)",
    0x20: "adaptive",
}

ANC_SET_VALUES = {
    "off": 0x01,
    "normal": 0x01,
    "transparent": 0x02,
    "trans": 0x02,
    "anc": 0x08,
    "on": 0x08,
}

BATTERY_NAMES = {1: "left", 2: "right", 3: "case"}
GESTURE_TYPES = {0x0201: "double", 0x0301: "triple", 0x0401: "hold"}
SIDE_NAMES = {1: "left", 2: "right"}


class ProtocolError(ValueError):
    """Raised when a buffer cannot be parsed as an aa frame."""


@dataclass(frozen=True)
class Frame:
    """One decoded aa frame."""

    flag: int
    cmd: int
    seq: int
    data: bytes
    raw: bytes

    @property
    def is_push(self) -> bool:
        return self.flag == FLAG_PUSH

    @property
    def is_response(self) -> bool:
        return bool(self.cmd & 0x8000)

    @property
    def base_cmd(self) -> int:
        """Command number with the reply bit cleared."""
        return self.cmd & 0x7FFF

    @property
    def category(self) -> int:
        return self.cmd & 0xFF

    @property
    def subcommand(self) -> int:
        return (self.cmd >> 8) & 0xFF

    @property
    def name(self) -> str:
        return COMMAND_NAMES.get(self.base_cmd, f"0x{self.base_cmd:04x}")

    def summary(self) -> str:
        if self.is_push:
            kind = "PUSH"
        elif self.is_response:
            kind = "RESP"
        else:
            kind = "CMD"
        return f"{kind:4} {self.name:13} seq=0x{self.seq:02x} len={len(self.data)}"


@dataclass(frozen=True)
class BatteryLevel:
    index: int
    percent: int
    charging: bool

    @property
    def name(self) -> str:
        return BATTERY_NAMES.get(self.index, f"id{self.index}")


def _write_varint(value: int) -> bytes:
    if value < 0:
        raise ValueError("length cannot be negative")
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _read_varint(buf: bytes, offset: int) -> tuple[int | None, int]:
    """Read a LEB128 value.

    Returns ``(None, offset)`` when the buffer ends inside the varint, so a
    caller can keep the partial frame for the next read.
    """
    value = 0
    shift = 0
    start = offset
    while offset < len(buf):
        byte = buf[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, offset
        shift += 7
        if shift > 7 * (_MAX_VARINT_BYTES - 1):
            raise ProtocolError("length varint is too long")
    return None, start


def encode(cmd: int, data: bytes = b"", seq: int = 0x01,
           flag: int = FLAG_COMMAND) -> bytes:
    """Build one frame.

    ``cmd`` is the canonical command number, e.g. ``0x0404`` for an ANC set.
    """
    if not 0 <= cmd <= 0x7FFF:
        raise ValueError(f"command out of range: 0x{cmd:x}")
    if not 0 <= seq <= 0xFF:
        raise ValueError("sequence byte must be 0..255")
    if flag not in (FLAG_COMMAND, FLAG_PUSH):
        raise ValueError("flag must be 0x00 or 0x08")

    payload = bytes(data)
    length = _FIXED_HEADER + len(payload)
    category = cmd & 0xFF
    subcommand = (cmd >> 8) & 0xFF
    return (
        bytes([SOF])
        + _write_varint(length)
        + bytes([
            flag,
            0x00,
            category,
            subcommand,
            seq & 0xFF,
            len(payload) & 0xFF,
            (len(payload) >> 8) & 0xFF,
        ])
        + payload
    )


def decode_stream(buf: bytes) -> tuple[list[Frame], bytes]:
    """Decode every complete frame in ``buf``.

    Returns ``(frames, remainder)``. Garbage between frames is skipped and a
    trailing partial frame is returned untouched for the next read.
    """
    data = bytes(buf)
    frames: list[Frame] = []
    i = 0
    while i < len(data):
        if data[i] != SOF:
            nxt = data.find(bytes([SOF]), i + 1)
            if nxt < 0:
                return frames, b""
            i = nxt
            continue

        try:
            length, after_len = _read_varint(data, i + 1)
        except ProtocolError:
            i += 1
            continue

        if length is None:
            break  # need more bytes for the length varint

        varint_len = after_len - (i + 1)
        total = 1 + varint_len + length
        if length < _FIXED_HEADER:
            i += 1
            continue
        if i + total > len(data):
            break  # incomplete frame; keep it

        if after_len + _FIXED_HEADER > i + total:
            i += 1
            continue

        flag = data[after_len]
        pad = data[after_len + 1]
        category = data[after_len + 2]
        subcommand = data[after_len + 3]
        seq = data[after_len + 4]
        dlen = data[after_len + 5] | (data[after_len + 6] << 8)

        valid = (
            pad == 0x00
            and flag in (FLAG_COMMAND, FLAG_PUSH)
            and length == _FIXED_HEADER + dlen
            and after_len + _FIXED_HEADER + dlen == i + total
        )
        if not valid:
            i += 1
            continue

        payload_start = after_len + _FIXED_HEADER
        raw = data[i:i + total]
        frames.append(Frame(
            flag=flag,
            cmd=category | (subcommand << 8),
            seq=seq,
            data=data[payload_start:payload_start + dlen],
            raw=raw,
        ))
        i += total

    return frames, data[i:]


def decode(data: bytes) -> Frame:
    """Decode exactly one frame, raising :class:`ProtocolError` otherwise."""
    frames, rest = decode_stream(data)
    if len(frames) != 1 or rest:
        raise ProtocolError("buffer is not exactly one complete frame")
    return frames[0]


# --------------------------------------------------------------------------
# Frame builders for the verified commands
# --------------------------------------------------------------------------

def anc_frame(mode: int | str, seq: int = 0x01) -> bytes:
    """ANC set frame. ``mode`` may be 0x01/0x02/0x08 or a name."""
    if isinstance(mode, str):
        try:
            mode = ANC_SET_VALUES[mode.lower()]
        except KeyError as exc:
            choices = ", ".join(sorted(ANC_SET_VALUES))
            raise ValueError(f"unknown ANC mode {mode!r}; choose {choices}") from exc
    return encode(CMD_ANC_SET, bytes([0x01, 0x01, mode & 0xFF]), seq)


def anc_status_frame(seq: int = 0x01) -> bytes:
    return encode(CMD_ANC_STATUS, b"\x01\x01", seq)


def battery_frame(seq: int = 0x01) -> bytes:
    return encode(CMD_BATTERY, b"", seq)


def firmware_frame(seq: int = 0x01) -> bytes:
    return encode(CMD_FIRMWARE, b"", seq)


def gestures_frame(seq: int = 0x01) -> bytes:
    return encode(CMD_GESTURES, b"", seq)


# --------------------------------------------------------------------------
# Payload parsers
# --------------------------------------------------------------------------

def parse_battery(data: bytes) -> list[BatteryLevel]:
    """Parse a 0x8106 reply or a 0x0204 push.

    Layout: ``status/type, count, (index, value) * count``. Bit 7 of each value
    is the charging flag; the low seven bits are the percentage.
    """
    if len(data) < 2:
        return []
    count = min(data[1], (len(data) - 2) // 2)
    levels = []
    for n in range(count):
        index = data[2 + 2 * n]
        raw = data[3 + 2 * n]
        levels.append(BatteryLevel(index, raw & 0x7F, bool(raw & 0x80)))
    return levels


def parse_firmware(data: bytes) -> list[str]:
    """Return the comma-separated version tokens from a 0x8105 reply."""
    if len(data) < 3:
        return []
    text = data[2:].split(b"\x00", 1)[0].decode("ascii", "replace")
    return [part for part in text.split(",") if part]


def parse_gestures(data: bytes) -> list[tuple[int, int, int]]:
    """Return ``(side, gesture_type, action)`` rows from a 0x8108 reply."""
    if len(data) < 2:
        return []
    count = min(data[1], (len(data) - 2) // 4)
    rows = []
    for n in range(count):
        off = 2 + 4 * n
        side = data[off]
        gesture_type = data[off + 1] | (data[off + 2] << 8)
        action = data[off + 3]
        rows.append((side, gesture_type, action))
    return rows


def parse_anc_mode(data: bytes) -> int | None:
    """Return the mode byte from a 0x810C reply (or the last byte of a push)."""
    if not data:
        return None
    if len(data) >= 4 and data[1] == 0x01 and data[2] == 0x01:
        return data[3]
    return data[-1]


def parse_ascii(data: bytes, skip: int = 1) -> str:
    """Decode a NUL-terminated ASCII field (device info and similar)."""
    raw = data[skip:].split(b"\x00", 1)[0]
    return raw.decode("ascii", "replace").strip()


def format_battery(levels: list[BatteryLevel]) -> str:
    parts = []
    for level in levels:
        mark = "+" if level.charging else ""
        parts.append(f"{level.name} {level.percent}%{mark}")
    return "  ".join(parts)


def format_gestures(rows: list[tuple[int, int, int]]) -> str:
    lines = []
    for side, gesture_type, action in rows:
        side_name = SIDE_NAMES.get(side, f"side{side}")
        type_name = GESTURE_TYPES.get(gesture_type, f"0x{gesture_type:04x}")
        lines.append(f"{side_name:5} {type_name:7} action={action}")
    return "\n".join(lines)
