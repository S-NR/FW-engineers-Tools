"""Checksum known-answer tests and metadata heuristics."""

import unittest

from firmware_binary_tool.core import checksum
from firmware_binary_tool.core.metadata_detector import compare_metadata, detect_metadata

from .helpers import image_from_bytes, image_from_segments

CHECK = b"123456789"


class ChecksumTests(unittest.TestCase):
    def test_known_answers(self):
        # Standard "check" values from the CRC catalogue for input "123456789"
        expected = {
            "CRC-32": "0xCBF43926",
            "CRC-16/CCITT-FALSE": "0x29B1",
            "CRC-16/XMODEM": "0x31C3",
            "CRC-16/MODBUS": "0x4B37",
            "CRC-16/ARC": "0xBB3D",
            "SHA-256": "15e2b0d3c33891ebb0f1ef609ec419420c20e320ce94c65fbc8c3312448eb225",
        }
        for alg, value in expected.items():
            with self.subTest(alg):
                self.assertEqual(checksum.checksum_bytes(alg, CHECK), value)

    def test_chunking_does_not_change_result(self):
        data = bytes(range(256)) * 50
        for alg in checksum.algorithm_names():
            whole = checksum.compute(alg, [data])
            parts = checksum.compute(alg, [data[i:i + 333] for i in range(0, len(data), 333)])
            self.assertEqual(whole, parts, alg)

    def test_image_checksum_gaps(self):
        img = image_from_segments([(0, b"1234"), (8, b"56789")])
        self.assertEqual(checksum.checksum_image("CRC-32", img), checksum.checksum_bytes("CRC-32", CHECK))
        filled = checksum.checksum_image("CRC-32", img, 0, 13, fill=0xFF)
        self.assertEqual(filled, checksum.checksum_bytes("CRC-32", b"1234\xff\xff\xff\xff56789"))

    def test_unknown_algorithm(self):
        with self.assertRaises(ValueError):
            checksum.checksum_bytes("CRC-99", b"")


class MetadataTests(unittest.TestCase):
    def _image(self, *strings: str):
        blob = b"\x00\x11\x22".join(s.encode() + b"\0" for s in strings)
        return image_from_bytes(b"\xff" * 16 + blob + b"\xff" * 16, base=0x08000000)

    def test_detection(self):
        img = self._image("FW_VERSION=1.2.3", "Build: Jan 12 2026 10:22:33", "git:3f9a2c1",
                          "Product: XC-200 Motor Controller", "Board: MCB-REV-C")
        items = detect_metadata(img)
        found = {(i.category, i.value) for i in items}
        self.assertIn(("Firmware version", "1.2.3"), found)
        self.assertIn(("Build date", "Jan 12 2026"), found)
        self.assertIn(("Build time", "10:22:33"), found)
        self.assertIn(("Git commit", "3f9a2c1"), found)
        self.assertIn(("Product name", "XC-200 Motor Controller"), found)
        self.assertIn(("Board name", "MCB-REV-C"), found)
        ver = next(i for i in items if i.value == "1.2.3")
        self.assertEqual(ver.confidence, "high")
        self.assertGreaterEqual(ver.address, 0x08000000)

    def test_compare_metadata(self):
        a = detect_metadata(self._image("version 1.2.3", "Jan 12 2026"))
        b = detect_metadata(self._image("version 1.2.4", "Jan 12 2026"))
        status = {m.category: m.status for m in compare_metadata(a, b)}
        self.assertEqual(status["Firmware version"], "CHANGED")
        self.assertEqual(status["Build date"], "SAME")
        self.assertEqual(status["Git commit"], "NOT FOUND")

    def test_no_strings(self):
        self.assertEqual(detect_metadata(image_from_bytes(bytes(range(4)) * 100)), [])


if __name__ == "__main__":
    unittest.main()
