# realme Buds T200 — protocol notes & local control

Local, account-free control of the **realme Buds T200** over Classic Bluetooth.
No Realme Link app, no login, no cloud. This repo also contains the protocol
notes and capture tooling I used to get there.

## What works

| Feature | Command | Status |
|---|---|---|
| ANC / transparency / off | `0x0404` | verified on T200 |
| ANC state | `0x010C` | verified on T200 |
| Battery (left / right / case) | `0x0106`, push `0x0204` | verified on T200 |
| Firmware versions | `0x0105` | verified on T200 |
| Touch gesture map | `0x0108` | verified on T200 |
| btsnoop HCI decoder | — | tested with synthetic HCI logs |

## Quick start

Requirements: Python 3.10+, `pyserial`, and the buds paired + connected for
audio. Windows then exposes SPP as a COM port.

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

python -m t200 ports
python -m t200 status --mac AA:BB:CC:DD:EE:FF
python -m t200 anc on --mac AA:BB:CC:DD:EE:FF
```

`anc` accepts `off`, `transparent`, and `on`. If auto-detection picks the wrong
port, pass `--port COM4` (or `/dev/rfcomm0` on Linux).

Real output from my buds:

```text
$ python -m t200 status
[*] ANC status
[>] aa0900000c011002000101
  RESP ANC status    seq=0x10 len=4  00010108
       mode=0x08 (ANC)
[*] Battery
[>] aa0700000601110000
  RESP battery       seq=0x11 len=8  0003023c01320300
       right 60%  left 50%  case 0%
[*] Firmware
  RESP firmware      seq=0x12 len=27  0002312c322c312e312e302e34372c322c322c312e312e302e3437
       1, 2, 1.1.0.47, 2, 2, 1.1.0.47
```

Decode an Android HCI snoop log:

```bash
python btsnoop_extract.py btsnoop_hci.log --mac AA:BB:CC:DD:EE:FF
```

## The protocol in one screen

```text
AA | LEN | FLAG | 00 | CAT | SUB | SEQ | DLEN (u16le) | DATA
```

- `LEN` is a LEB128 varint equal to `7 + len(DATA)`. It counts every byte after
  itself, so a frame is `1 + len(varint) + LEN` bytes. Small frames use one byte;
  large replies use two — the usual source of parser desync.
- `FLAG` is `0x00` for commands/replies and `0x08` for unsolicited pushes.
- The command is `CAT | (SUB << 8)`. A reply sets bit 7 of `SUB`, so `0x0106`
  (battery query) is answered with `0x8106`.
- ANC on: `aa 0a 00 00 04 04 01 03 00 01 01 08`.

More detail in [`docs/PROTOCOL.md`](docs/PROTOCOL.md).

## How I did it

1. Captured Realme Link's Bluetooth traffic with Android's HCI snoop log.
2. Extracted the RFCOMM payloads, changed one setting at a time, and diffed.
3. Inspected the Realme Link APK for the OPPO/realme SPP layer and Airoha SDK.
4. Replayed read-only frames against my own buds and kept only verified results.
5. Packaged the working subset here so it can be reproduced.

Dead ends (BLE RACE, firmware dumping, OTA auth) are written down in
[`docs/NOTES.md`](docs/NOTES.md) so the next person does not repeat them.

## Layout

```text
t200/protocol.py       frame codec + verified command payloads
t200/spp.py            Classic Bluetooth SPP transport
t200/cli.py            python -m t200
btsnoop_extract.py     HCI capture -> decoded aa frames
docs/PROTOCOL.md       protocol notes
docs/NOTES.md          research notes / dead ends
tests/                 codec tests built from real captured frames
```

Run the tests with:

```bash
python -m unittest discover -s tests -v
```

## Scope & safety

This is interoperability work on my own hardware. It only uses the buds'
control channel: it does not flash firmware, bypass authentication, or touch
the OTA service. `raw` can send arbitrary frames and may change device state —
use it at your own risk.

Not affiliated with realme. Realme and Buds are trademarks of their owners.

## License

MIT
