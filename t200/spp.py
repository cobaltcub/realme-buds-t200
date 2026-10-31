"""Classic Bluetooth SPP transport for the buds.

On Windows the paired buds' RFCOMM channel 1 (SPP, UUID 0x1101) is exposed as
a virtual COM port. On Linux the equivalent is usually ``/dev/rfcomm0`` after
``rfcomm connect``. This module only needs pyserial.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from .protocol import Frame, decode_stream

SPP_UUID = "00001101-0000-1000-8000-00805f9b34fb"

_MAC_RE = re.compile(
    r"(?<![0-9A-F])(?:[0-9A-F]{2}[:-]){5}[0-9A-F]{2}(?![0-9A-F])",
    re.IGNORECASE,
)
# Windows puts the remote address in the device instance path:
#   ...\8&93B9F31&0&AABBCCDDEEFF_C00000000
_INSTANCE_MAC_RE = re.compile(r"&([0-9A-F]{12})(?:_|$|\\)", re.IGNORECASE)


@dataclass(frozen=True)
class Port:
    device: str
    description: str
    hwid: str
    mac: str | None = None

    def line(self) -> str:
        mac = f"  {self.mac}" if self.mac else ""
        return f"{self.device:<8} {self.description}{mac}"


def normalize_mac(value: str) -> str:
    """Return ``AA:BB:CC:DD:EE:FF`` from any common MAC spelling."""
    compact = re.sub(r"[^0-9A-Fa-f]", "", value).upper()
    if len(compact) != 12:
        raise ValueError(f"not a Bluetooth MAC address: {value!r}")
    return ":".join(compact[i:i + 2] for i in range(0, 12, 2))


def extract_mac(text: str) -> str | None:
    match = _MAC_RE.search(text)
    if match:
        return normalize_mac(match.group(0))
    match = _INSTANCE_MAC_RE.search(text)
    if match:
        candidate = match.group(1).upper()
        if candidate != "000000000000":
            return normalize_mac(candidate)
    return None


def available_ports() -> list[Port]:
    """List serial ports and any MAC address found in their device path."""
    try:
        from serial.tools import list_ports
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise RuntimeError(
            "pyserial is required: pip install -r requirements.txt"
        ) from exc

    ports = []
    for info in list_ports.comports():
        text = " ".join(filter(None, [
            info.hwid or "",
            info.description or "",
            getattr(info, "interface", None) or "",
            getattr(info, "location", None) or "",
        ]))
        ports.append(Port(
            device=info.device,
            description=info.description or "",
            hwid=info.hwid or "",
            mac=extract_mac(text),
        ))
    return ports


def _looks_bluetooth(port: Port) -> bool:
    text = f"{port.hwid} {port.description}".upper()
    return "BTHENUM" in text or "BLUETOOTH" in text or "1101" in text


def find_port(mac: str | None = None, port: str | None = None) -> str:
    """Resolve a COM port from ``--port``, ``--mac``, or the SPP device path."""
    if port:
        return port

    ports = available_ports()
    candidates = [p for p in ports if _looks_bluetooth(p)]
    pool = candidates or ports

    if mac:
        target = normalize_mac(mac)
        matches = [p for p in pool if p.mac == target]
        if matches:
            return matches[0].device
        seen = ", ".join(p.line() for p in pool) or "none"
        raise RuntimeError(
            f"No serial port found for {target}. Ports seen: {seen}. "
            "Pair and connect the buds for audio first; Windows creates the "
            "SPP COM port on demand."
        )

    spp = [p for p in candidates if "1101" in p.hwid.upper()]
    if len(spp) == 1:
        return spp[0].device
    if len(pool) == 1:
        return pool[0].device

    if not pool:
        raise RuntimeError(
            "No Bluetooth serial port found. Pair/connect the buds, then use "
            "`python -m t200 ports`. On Linux pass --port /dev/rfcomm0."
        )
    lines = "\n".join(f"  {p.line()}" for p in pool)
    raise RuntimeError(
        "Several serial ports are available; choose one with --port:\n" + lines
    )


class Buds:
    """A small synchronous SPP client.

    Example::

        with Buds("COM4") as buds:
            for frame in buds.transact(anc_frame("anc")):
                print(frame.summary())
    """

    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 2.0):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self._serial = None
        self._buffer = b""

    def __enter__(self) -> "Buds":
        self.open()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def open(self) -> None:
        try:
            import serial
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "pyserial is required: pip install -r requirements.txt"
            ) from exc

        self._serial = serial.Serial(
            self.port,
            baudrate=self.baudrate,
            timeout=self.timeout,
            write_timeout=3.0,
        )
        self._buffer = b""
        try:
            self._serial.reset_input_buffer()
            self._serial.reset_output_buffer()
        except Exception:
            pass

    def close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            finally:
                self._serial = None

    def send(self, frame: bytes) -> None:
        if self._serial is None:
            raise RuntimeError("port is not open")
        self._serial.write(frame)
        self._serial.flush()

    def read(self, timeout: float | None = None, settle: float = 0.25) -> list[Frame]:
        """Read until the line is quiet, then decode complete frames."""
        if self._serial is None:
            raise RuntimeError("port is not open")

        timeout = self.timeout if timeout is None else timeout
        deadline = time.monotonic() + timeout
        last_data = 0.0

        while time.monotonic() < deadline:
            waiting = self._serial.in_waiting
            if waiting:
                self._buffer += self._serial.read(waiting)
                last_data = time.monotonic()
            elif self._buffer and last_data and time.monotonic() - last_data >= settle:
                break
            time.sleep(0.02)

        frames, self._buffer = decode_stream(self._buffer)
        return frames

    def transact(self, frame: bytes, timeout: float | None = None) -> list[Frame]:
        self.send(frame)
        return self.read(timeout)
