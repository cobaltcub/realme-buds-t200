"""Port/MAC parsing tests (no hardware or pyserial needed)."""

import unittest

from t200.spp import extract_mac, normalize_mac


class MacTests(unittest.TestCase):
    def test_normalize_colon(self):
        self.assertEqual(normalize_mac("aa-bb-cc-dd-ee-ff"), "AA:BB:CC:DD:EE:FF")

    def test_windows_instance_path(self):
        hwid = (
            r"BTHENUM\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&005D"
            r"\8&93B9F31&0&AABBCCDDEEFF_C00000000"
        )
        self.assertEqual(extract_mac(hwid), "AA:BB:CC:DD:EE:FF")

    def test_does_not_match_uuid(self):
        hwid = (
            r"BTHENUM\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0000"
            r"\8&93B9F31&0&000000000000_0000001C"
        )
        self.assertIsNone(extract_mac(hwid))

    def test_invalid_mac(self):
        with self.assertRaises(ValueError):
            normalize_mac("not-a-mac")


if __name__ == "__main__":
    unittest.main()
