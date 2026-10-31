"""Codec tests built from real captured T200 frames."""

import unittest

from t200 import protocol as p

# Real frames from a live realme Buds T200 session.
ANC_SET = "aa0a00000404010300010108"
ANC_ACK = "aa080000048401010000"
ANC_QUERY = "aa0900000c011002000101"
ANC_STATUS = "aa0b00000c8110040000010101"
BATTERY_QUERY = "aa0700000601110000"
BATTERY_REPLY = "aa0f000006811108000003026401640300"
BATTERY_PUSH = "aa0f080004021b08000103015002500300"
FIRMWARE_QUERY = "aa0700000501100000"
FIRMWARE_REPLY = (
    "aa2200000581101b000002312c322c312e312e302e34372c"
    "322c322c312e312e302e3437"
)
GESTURE_QUERY = "aa0700000801100000"
GESTURE_REPLY = (
    "aa2100000881101a000006010102010101030601010408"
    "020102010201030602010408"
)
DEVICE_INFO = "aa0b0000048105040000313134"


class EncodeTests(unittest.TestCase):
    def test_anc_set(self):
        self.assertEqual(p.anc_frame("anc", seq=0x01).hex(), ANC_SET)
        self.assertEqual(p.anc_frame(0x08, seq=0x01).hex(), ANC_SET)

    def test_battery_query(self):
        self.assertEqual(p.battery_frame(seq=0x11).hex(), BATTERY_QUERY)

    def test_firmware_query(self):
        self.assertEqual(p.firmware_frame(seq=0x10).hex(), FIRMWARE_QUERY)

    def test_large_payload_uses_two_byte_varint(self):
        payload = bytes(range(256)) + bytes(14)
        frame = p.encode(p.CMD_SETTINGS, payload, seq=0x10)
        self.assertEqual(frame[:4].hex(), "aa950200")
        self.assertEqual(len(frame), 280)
        decoded = p.decode(frame)
        self.assertEqual(decoded.data, payload)


class DecodeTests(unittest.TestCase):
    def test_anc_ack(self):
        frame = p.decode(bytes.fromhex(ANC_ACK))
        self.assertTrue(frame.is_response)
        self.assertFalse(frame.is_push)
        self.assertEqual(frame.base_cmd, p.CMD_ANC_SET)
        self.assertEqual(frame.data, b"\x00")
        self.assertEqual(frame.seq, 0x01)

    def test_battery_reply(self):
        frame = p.decode(bytes.fromhex(BATTERY_REPLY))
        self.assertEqual(frame.base_cmd, p.CMD_BATTERY)
        levels = p.parse_battery(frame.data)
        self.assertEqual(
            [(x.index, x.percent, x.charging) for x in levels],
            [(2, 100, False), (1, 100, False), (3, 0, False)],
        )

    def test_battery_push(self):
        frame = p.decode(bytes.fromhex(BATTERY_PUSH))
        self.assertTrue(frame.is_push)
        self.assertEqual(frame.base_cmd, p.CMD_BATTERY_PUSH)
        levels = p.parse_battery(frame.data)
        self.assertEqual(
            [(x.index, x.percent, x.charging) for x in levels],
            [(1, 80, False), (2, 80, False), (3, 0, False)],
        )

    def test_firmware_reply(self):
        frame = p.decode(bytes.fromhex(FIRMWARE_REPLY))
        self.assertEqual(frame.base_cmd, p.CMD_FIRMWARE)
        self.assertEqual(
            p.parse_firmware(frame.data),
            ["1", "2", "1.1.0.47", "2", "2", "1.1.0.47"],
        )

    def test_gesture_reply(self):
        frame = p.decode(bytes.fromhex(GESTURE_REPLY))
        self.assertEqual(frame.base_cmd, p.CMD_GESTURES)
        rows = p.parse_gestures(frame.data)
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[0], (1, 0x0201, 1))
        self.assertEqual(rows[-1], (2, 0x0401, 8))

    def test_anc_status(self):
        frame = p.decode(bytes.fromhex(ANC_STATUS))
        self.assertEqual(frame.base_cmd, p.CMD_ANC_STATUS)
        self.assertEqual(p.parse_anc_mode(frame.data), 0x01)

    def test_device_info(self):
        frame = p.decode(bytes.fromhex(DEVICE_INFO))
        self.assertEqual(frame.base_cmd, p.CMD_DEVICE_INFO)
        self.assertEqual(p.parse_ascii(frame.data, skip=1), "114")

    def test_partial_stream(self):
        first = bytes.fromhex(ANC_QUERY)
        second = p.battery_frame(seq=0x11)
        frames, rest = p.decode_stream(first + second[:5])
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].base_cmd, p.CMD_ANC_STATUS)
        self.assertEqual(rest, second[:5])

        frames, rest = p.decode_stream(rest + second[5:])
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].base_cmd, p.CMD_BATTERY)
        self.assertEqual(rest, b"")

    def test_garbage_is_skipped(self):
        frames, rest = p.decode_stream(b"\x00\xff" + bytes.fromhex(ANC_ACK))
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].base_cmd, p.CMD_ANC_SET)
        self.assertEqual(rest, b"")

    def test_rejects_bad_length(self):
        bad = bytearray.fromhex(ANC_ACK)
        bad[7] = 0x02  # payload length no longer matches the frame length
        with self.assertRaises(p.ProtocolError):
            p.decode(bytes(bad))


if __name__ == "__main__":
    unittest.main()
