# Research notes

These are the condensed notes from the T200 work. The goal was a local ANC and
status client, not a firmware exploit, so the scope stops at the control
protocol.

## Method

1. Enabled Android's **Bluetooth HCI snoop log** and used Realme Link normally.
2. Parsed the btsnoop file, rebuilt L2CAP/RFCOMM, and pulled out UIH payloads.
3. Changed one setting at a time and diffed the resulting `aa` frames.
4. Inspected the Realme Link APK (`com.realme.link`, plus
   `split_realmeHeadset.apk`) for the OPPO/realme SPP layer and the Airoha SDK.
5. Replayed only read-only frames against my own buds, then verified each write
   by reading the state back.

The useful correction from the live data: the frame length is a **LEB128
varint**, not a fixed byte. Small frames hide this because the high bit is
clear. The 165-byte settings reply was the first frame that exposed it.

## Dead ends

### Airoha RACE / firmware dump

- The Realme Link APK contains `com.airoha.android.lib` with RACE, FOTA, MMI,
  and flash-read code. That made a firmware dump look promising.
- RACE over SPP was silent on every open channel. A full RFCOMM scan found only
  channels **1, 5, and 6** open.
- Channel 5 is broadcast-only: it pushes `03 <seq> 00 <len>` hello triples on
  connect and ignores writes.
- Pre-bond BLE GATT exposed Fast Pair (`FE2C`) and a vendor service
  (`000008a4`) with a static identity blob — not the Airoha MMI service
  (`5052494D-...`).
- I stopped before the BLE bond + RACE/exploit path. **No firmware was dumped.**

### OTA service

- The app calls `POST /api/v3/ota/checkUpdate` on
  `iot-{cn,in,eu,us}-ota.realme.com`.
- The request needs a HeyTap SSO session (`X-Authorization`, `otoken`); the
  server rejects anonymous requests. I did not attempt to bypass that.

### BLE identity

- Fast Pair advertises a rotating private address ending in `-GFP`.
- The main LE identity differs from the Classic MAC by one byte and only
  advertises when Classic is disconnected.
- Not needed for the final SPP client, so it stayed out of the tool.

## Safety

- A blind sweep of the `0x06xx` family shut the buds down once. Do not scan
  undocumented command families on live hardware.
- Keep writes to state you can restore (ANC mode, gestures). Read-only queries
  are safe.
- If the buds stop responding, put them back in the case and reconnect.

## Corrections

- **2026-11-01 — multipoint.** The first version of `PROTOCOL.md` said the buds
  hold one Classic connection at a time. That was wrong. They keep two active
  Classic connections, and control frames (ANC and others) were accepted from
  both the phone and the laptop. Audio routing is handled separately by the
  buds' multipoint logic. The transport section has been corrected.

## Prior art

- [funinkina/realme-t310-anc-controls](https://github.com/funinkina/realme-t310-anc-controls)
  — initial T310 framing and the SPP ANC idea.
- [Gadgetbridge](https://github.com/gadgetbridge/gadgetbridge) — the
  `OppoHeadphonesProtocol` implementation and command names.
- [Leaf-lsgtky/OppoPods](https://github.com/Leaf-lsgtky/OppoPods) — clear write-up
  of the OPPO/realme packet shape.
- [anikket-b/Realme-TWS-Mac-Controller](https://github.com/anikket-b/Realme-TWS-Mac-Controller)
  — sibling-model command map and the ANC mode values.

Every frame in this repo was re-verified on the T200; the prior art was used to
know where to look, not as a substitute for testing.
