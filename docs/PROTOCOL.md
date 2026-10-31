# Protocol notes

Everything below was verified against a realme Buds T200 over Classic Bluetooth
SPP. Frames are shown as hex bytes, with the command number also given in the
usual `0xCATSUB` notation.

## Transport

- Classic Bluetooth **SPP / RFCOMM channel 1**, UUID
  `00001101-0000-1000-8000-00805f9b34fb`.
- Windows exposes the paired buds' SPP channel as a COM port; Linux usually
  uses `/dev/rfcomm0`.
- No login, handshake, or account. The buds answer as soon as the SPP channel
  is open.
- Multipoint is supported: the buds can keep **two Classic connections active
  at once**. On my unit, ANC and other control frames were accepted while both
  the phone and the laptop were connected, so the earlier "disconnect the other
  device first" note was wrong.
- Audio routing/focus between the two devices is handled separately by the buds'
  multipoint logic. It is independent of whether a control frame is accepted.

## Frame format

```text
AA | LEN | FLAG | 00 | CAT | SUB | SEQ | DLEN (u16le) | DATA
```

| Field | Size | Notes |
|---|---|---|
| `AA` | 1 | start of frame |
| `LEN` | 1–2 | LEB128 varint, `7 + len(DATA)` |
| `FLAG` | 1 | `0x00` command/reply, `0x08` unsolicited push |
| pad | 1 | always `0x00` |
| `CAT` | 1 | command category |
| `SUB` | 1 | sub-command; bit 7 set in replies |
| `SEQ` | 1 | host-chosen, echoed by the buds |
| `DLEN` | 2 | payload length, little-endian |
| `DATA` | `DLEN` | payload |

The command number is `CAT | (SUB << 8)`: `0x0106` is `06 01` on the wire, and
its reply is `06 81` (`0x8106`). There is no checksum.

`LEN` counts every byte **after the length field**:

```text
frame length = 1 (SOF) + len(varint) + LEN
```

For small frames that is `LEN + 2`. For `LEN >= 128` the varint takes two bytes,
so the frame is `LEN + 3`. This is the easiest place to desync a parser.

Example — ANC on:

```text
aa 0a 00 00 04 04 01 03 00 01 01 08
   |  |           |  |     |  |  |  |
   |  |           |  |     |  |  |  +-- mode 0x08 = ANC
   |  |           |  |     |  |  +----- 0x01
   |  |           |  |     |  +-------- 0x01
   |  |           |  |     +----------- DLEN = 3
   |  |           |  +----------------- SEQ = 1
   |  |           +-------------------- SUB = 0x04, CAT = 0x04 -> cmd 0x0404
   |  +-------------------------------- FLAG = 0, pad = 0
   +----------------------------------- LEN = 10 = 7 + 3
```

Example — large reply with a two-byte varint:

```text
aa 95 02 00 00 05 82 12 0e 01 ...
```

`0x95 0x02` decodes as `(0x15) | (0x02 << 7) = 277`. The frame is 280 bytes,
not 279, and carries 270 payload bytes.

## Verified commands

| Command | Request bytes | Reply | Payload |
|---|---|---|---|
| `0x0104` device info | `04 01` | `04 81` | `00 "114"` on this unit |
| `0x0105` firmware | `05 01` | `05 81` | `00 02 "<csv>"` |
| `0x0106` battery | `06 01` | `06 81` | `00 <count> (id, pct)*` |
| `0x0108` gesture map | `08 01` | `08 81` | `00 <count> (side, type, action)*` |
| `0x010C` ANC state | `0c 01` + `01 01` | `0c 81` | `00 01 01 <mode>` |
| `0x0401` settings table | `01 04` | `01 84` | large state table |
| `0x0404` ANC set | `04 04` + `01 01 <mode>` | `04 84` | `<status>` |
| `0x0204` battery push | — | `04 02`, flag `08` | `01 <count> (id, pct)*` |
| `0x050E` heartbeat push | — | `0e 05`, flag `08` | counters |

Replies echo the request's `SEQ`. Pushes carry their own sequence byte.

## ANC

Set payload: `01 01 <mode>`. Reply payload is a single status byte (`00` = ok).

| Mode | Meaning |
|---|---|
| `0x01` | off / normal |
| `0x02` | transparency |
| `0x08` | ANC (verified on the T200) |
| `0x04` | ANC mild — sibling BBK models, not verified here |
| `0x10` | ANC moderate — sibling BBK models, not verified here |
| `0x20` | adaptive — sibling BBK models, not verified here |

The `0x010C` query returns `00 01 01 <mode>`; the last byte uses the same table.

## Battery

Both the `0x8106` reply and the `0x0204` push carry pairs of bytes:

```text
id, value
```

- `id` 1 = left, 2 = right, 3 = case.
- bit 7 of `value` = charging.
- low seven bits = percentage.

Live example payload (`00 03 02 3c 01 32 03 00`):

```text
00        status
03        count
02 3c     right: 0x3c = 60%
01 32     left:  0x32 = 50%
03 00     case:  0%
```

A value with bit 7 set means charging, e.g. `81` = 1% and charging.

## Firmware

`0x0105` reply payload starts `00 02` and then contains an ASCII,
comma-separated version list:

```text
00 02 31 2c 32 2c 31 2e 31 2e 30 2e 34 37 2c 32 2c 32 2c 31 2e 31 2e 30 2e 34 37
      "1,2,1.1.0.47,2,2,1.1.0.47"
```

On this unit the six tokens form two groups of three. I did not establish what
each group means; the tool prints the tokens verbatim rather than guessing.

## Gestures

`0x0108` reply payload:

```text
00 <count> (side, type_u16le, action) * count
```

- `side` 1 = left, 2 = right.
- `type` `0x0201` = double click, `0x0301` = triple click, `0x0401` = hold.
- `action` is a model/firmware-specific id. Observed values on my unit include
  1, 6, 8, 11, and 12; I did not map them to named actions.

Example row `01 01 02 01` = left, type `0x0201`, action `1`.

## Device info

`0x0104` returned `00 "114"` on this T200. It is likely a model/product string;
treat it as opaque.

## Large state tables

`0x0401` returns a large table (the capture I kept is 165 payload bytes). It
uses the two-byte varint described above. I did not decode the table's fields;
the repo only documents it because it is useful for testing frame parsers.

## Safety

Small read-only queries are safe. Avoid blind scans of undocumented categories:
one exploratory sweep of the `0x06xx` family shut the buds down during this
work. Stick to the verified commands above, and use `raw` only when you can
afford to reset the buds by putting them back in the case.
