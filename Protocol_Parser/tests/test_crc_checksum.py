import unittest

import tests  # noqa: F401
from core import checksum
from core.crc import (PRESETS, CRCError, compute_crc, crc_to_bytes, find_matching_crc, get_preset, resolve_crc)
from tests.helpers import EXAMPLE_PACKET

CHECK_INPUT = b"123456789"


class CRCTests(unittest.TestCase):
    def test_all_presets_match_standard_check_values(self):
        for name, params in PRESETS.items():
            with self.subTest(name):
                self.assertEqual(compute_crc(CHECK_INPUT, params), params.check)

    def test_required_presets_exist(self):
        for name in ("CRC-8", "CRC-16", "CRC-16/CCITT", "CRC-16/MODBUS", "CRC-32"):
            self.assertIn(name, PRESETS)

    def test_well_known_values(self):
        self.assertEqual(compute_crc(CHECK_INPUT, "CRC-32"), 0xCBF43926)
        self.assertEqual(compute_crc(CHECK_INPUT, "CRC-16/MODBUS"), 0x4B37)
        self.assertEqual(compute_crc(CHECK_INPUT, "CRC-16/CCITT"), 0x29B1)
        # Classic Modbus request: 01 03 00 00 00 0A -> CRC C5 CD (low byte first)
        crc = compute_crc(bytes.fromhex("01030000000A"), "CRC-16/MODBUS")
        self.assertEqual(crc_to_bytes(crc, 16, "little"), bytes.fromhex("C5CD"))

    def test_example_packet_crc(self):
        crc = compute_crc(EXAMPLE_PACKET[1:12], "CRC-16/MODBUS")
        self.assertEqual(crc_to_bytes(crc, 16, "little"), bytes.fromhex("DD1F"))

    def test_aliases(self):
        self.assertEqual(get_preset("crc16").name, "CRC-16")
        self.assertEqual(get_preset("CRC-16/CCITT-FALSE").name, "CRC-16/CCITT")
        self.assertEqual(get_preset("modbus").name, "CRC-16/MODBUS")
        with self.assertRaises(CRCError):
            get_preset("CRC-99")

    def test_custom_parameters(self):
        # XMODEM described by hand must equal the preset.
        params = resolve_crc({"algorithm": "custom", "width": 16, "poly": "0x1021", "init": "0x0000",
                              "xorout": 0, "refin": False, "refout": False})
        self.assertEqual(compute_crc(CHECK_INPUT, params), 0x31C3)
        # Reflected 8-bit custom = CRC-8/MAXIM
        params = resolve_crc({"algorithm": "custom", "width": 8, "poly": 0x31, "refin": True, "refout": True})
        self.assertEqual(compute_crc(CHECK_INPUT, params), 0xA1)

    def test_override_marks_modified(self):
        params = resolve_crc({"algorithm": "CRC-16/MODBUS", "init": "0x0000"})
        self.assertIn("modified", params.name)
        self.assertEqual(compute_crc(CHECK_INPUT, params), 0xBB3D)  # == CRC-16/ARC

    def test_invalid_parameters(self):
        with self.assertRaises(CRCError):
            resolve_crc({"algorithm": "custom", "width": 8, "poly": "0x1FF"})
        with self.assertRaises(CRCError):
            resolve_crc({"algorithm": "custom", "poly": "0x1021"})  # width missing
        with self.assertRaises(CRCError):
            resolve_crc({"algorithm": "custom", "width": 4, "poly": 3})

    def test_find_matching_crc(self):
        matches = find_matching_crc([EXAMPLE_PACKET])
        self.assertEqual(len(matches), 1)
        m = matches[0]
        self.assertEqual((m.algorithm, m.range_start, m.range_end, m.crc_offset, m.byte_order),
                         ("CRC-16/MODBUS", 1, 11, 12, "little"))


class ChecksumTests(unittest.TestCase):
    DATA = bytes([0x01, 0x02, 0x03, 0xFF])

    def test_xor(self):
        self.assertEqual(checksum.xor8(self.DATA), 0x01 ^ 0x02 ^ 0x03 ^ 0xFF)

    def test_sum8_wraps(self):
        self.assertEqual(checksum.sum8(self.DATA), 0x05)  # 0x105 & 0xFF

    def test_sum16(self):
        self.assertEqual(checksum.sum16(self.DATA), 0x105)

    def test_twos_complement_8bit(self):
        cs = checksum.twos_complement8(self.DATA)
        self.assertEqual((sum(self.DATA) + cs) & 0xFF, 0)
        # Intel HEX record ":0300300002337A1E" -> checksum 0x1E
        self.assertEqual(checksum.twos_complement8(bytes.fromhex("0300300002337A")), 0x1E)

    def test_ones_complement_16bit_rfc1071(self):
        # IPv4 header example (checksum field zeroed) -> 0xB861
        header = bytes.fromhex("450000730000400040110000c0a80001c0a800c7")
        self.assertEqual(checksum.ones_complement16(header), 0xB861)

    def test_lookup(self):
        self.assertEqual(checksum.get_checksum("XOR").name, "xor8")
        self.assertEqual(checksum.compute_checksum("sum", b"\x10\x20"), 0x30)
        with self.assertRaises(ValueError):
            checksum.get_checksum("md5")

    def test_find_matching_checksum(self):
        pkts = [bytes.fromhex(h) for h in ("AA55010200EB01C2A555", "AA55010200EC01C4A558")]
        names = {m.algorithm for m in checksum.find_matching_checksum(pkts)}
        self.assertIn("sum8", names)


if __name__ == "__main__":
    unittest.main()
