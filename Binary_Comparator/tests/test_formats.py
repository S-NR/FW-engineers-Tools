"""Tests for BIN / Intel HEX / ELF loading and format detection."""

import hashlib
import os
import unittest

from firmware_binary_tool.core.binary_comparator import compare_images
from firmware_binary_tool.core.binary_reader import detect_format, load_firmware
from firmware_binary_tool.core.elf_compare import compare_elf
from firmware_binary_tool.errors import FirmwareFormatError, FirmwareToolError
from firmware_binary_tool.formats.elf_builder import RODATA, RWDATA, TEXT, SectionSpec, build_elf32
from firmware_binary_tool.formats.elf_parser import parse_elf_bytes
from firmware_binary_tool.formats.hex_parser import write_intel_hex

from .helpers import TempDirTestCase


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class BinTests(TempDirTestCase):
    def test_load_bin_with_base_and_hash(self):
        data = bytes(range(256)) * 4
        p = self.write("fw.bin", data)
        img = load_firmware(p, base_address=0x08000000)
        try:
            self.assertEqual(img.format, "bin")
            self.assertEqual(img.sha256, hashlib.sha256(data).hexdigest())
            self.assertEqual(img.address_range, (0x08000000, 0x080003FF))
            self.assertEqual(img.byte_at(0x08000001), 1)
            self.assertIsNone(img.byte_at(0x07FFFFFF))
            self.assertEqual(img.file_offset_of(0x08000010), 0x10)
            self.assertEqual(img.address_of_file_offset(0x10), 0x08000010)
            self.assertEqual(img.read(0x080003FE, 4), [0xFE, 0xFF, None, None])
            self.assertEqual(img.find(b"\x10\x11", 0x08000000), 0x08000010)
            self.assertEqual(img.find(b"\x10\x11", 0x08000011), 0x08000110)
            self.assertEqual(img.find(b"\x10\x11", 0x08000110, backwards=True), 0x08000010)
        finally:
            img.close()

    def test_file_is_never_modified(self):
        data = os.urandom(5000)
        p1, p2 = self.write("a.bin", data), self.write("b.bin", data[::-1])
        before = (os.path.getmtime(p1), _sha(p1))
        a, b = load_firmware(p1), load_firmware(p2)
        compare_images(a, b)
        a.close()
        b.close()
        after = (os.path.getmtime(p1), _sha(p1))
        self.assertEqual(before, after)

    def test_empty_bin(self):
        img = load_firmware(self.write("empty.bin", b""))
        self.assertTrue(img.is_empty)
        self.assertIsNone(img.address_range)
        self.assertTrue(img.warnings)

    def test_missing_file(self):
        with self.assertRaises(FirmwareToolError):
            load_firmware(self.path("does_not_exist.bin"))

    def test_iter_range_fill(self):
        img = load_firmware(self.write("x.bin", b"\x01\x02"), base_address=0x10)
        self.assertEqual(b"".join(img.iter_range(0x0E, 0x14, fill=0xFF)), b"\xFF\xFF\x01\x02\xFF\xFF")
        self.assertEqual(b"".join(img.iter_range()), b"\x01\x02")
        img.close()


class HexTests(TempDirTestCase):
    def test_roundtrip_and_extended_address(self):
        segs = [(0x0800FFF0, bytes(range(32))), (0x08020000, b"\xAA" * 5)]
        p = self.path("fw.hex")
        write_intel_hex(segs, p, start_linear=0x08000101)
        img = load_firmware(p)
        self.assertEqual(img.format, "hex")
        self.assertEqual([(s.start, bytes(s.data)) for s in img.segments], segs)
        self.assertIsNone(img.file_offset_of(0x08020000))  # HEX has no byte-level file offset
        self.assertEqual(img.info["Start linear address"], "0x08000101")

    def test_hex_equals_bin(self):
        data = os.urandom(3000)
        pb = self.write("fw.bin", data)
        ph = self.path("fw.hex")
        write_intel_hex([(0x08000000, data)], ph)
        r = compare_images(load_firmware(pb, base_address=0x08000000), load_firmware(ph))
        self.assertTrue(r.identical)

    def test_extended_segment_address(self):
        # record 02: segment 0x1000 -> base 0x10000
        text = ":020000021000EC\n:0400000001020304F2\n:00000001FF\n"
        img = load_firmware(self.write("seg.hex", text))
        self.assertEqual(img.segments[0].start, 0x10000)
        self.assertEqual(bytes(img.segments[0].data), b"\x01\x02\x03\x04")

    def test_malformed(self):
        cases = {
            "checksum": ":0400000001020304F3\n:00000001FF\n",
            "no colon": "0400000001020304F2\n",
            "bad hex": ":04000000010203ZZF2\n",
            "length": ":0500000001020304F2\n",
            "type": ":0400000701020304EB\n",
        }
        for name, text in cases.items():
            with self.subTest(name), self.assertRaises(FirmwareFormatError):
                load_firmware(self.write(f"{name}.hex", text))

    def test_overlapping_records_rejected(self):
        text = ":0400000001020304F2\n:0400020001020304F0\n:00000001FF\n"
        with self.assertRaises(FirmwareFormatError):
            load_firmware(self.write("ovl.hex", text))

    def test_missing_eof_warns(self):
        img = load_firmware(self.write("noeof.hex", ":0400000001020304F2\n"))
        self.assertTrue(any("EOF" in w for w in img.warnings))

    def test_binary_garbage_as_hex(self):
        with self.assertRaises(FirmwareFormatError):
            load_firmware(self.write("garbage.hex", b"\x00\xff\x13\x80" * 10))

    def test_detect_format(self):
        self.assertEqual(detect_format(self.write("a.dat", ":00000001FF\n")), "hex")
        self.assertEqual(detect_format(self.write("b.dat", b"\x7fELF" + bytes(60))), "elf")
        self.assertEqual(detect_format(self.write("c.dat", b"\x00\x01")), "bin")
        self.assertEqual(detect_format(self.write("d.hex", b"")), "hex")


def sample_elf(text: bytes, bss_size=0x100, extra_symbol=False) -> bytes:
    sections = [
        SectionSpec(".text", 0x08000000, text, flags=TEXT),
        SectionSpec(".rodata", 0x08001000, b"ROM DATA v1.0.0\0", flags=RODATA),
        SectionSpec(".data", 0x20000000, b"\x11\x22\x33\x44", flags=RWDATA, lma=0x08002000),
        SectionSpec(".bss", 0x20000004, None, size=bss_size, flags=RWDATA),
    ]
    syms = [("main", 0x08000001, 0x10, "FUNC", ".text"), ("g_var", 0x20000000, 4, "OBJECT", ".data")]
    if extra_symbol:
        syms.append(("new_func", 0x08000011, 0x8, "FUNC", ".text"))
    return build_elf32(sections, syms, entry=0x08000001)


class ElfTests(TempDirTestCase):
    def test_parse_sections_symbols_and_segments(self):
        p = self.write("fw.elf", sample_elf(b"\x00\xbf" * 16))
        img = load_firmware(p)
        self.assertEqual(img.format, "elf")
        names = [s.name for s in img.elf.sections]
        for n in (".text", ".rodata", ".data", ".bss", ".symtab", ".comment"):
            self.assertIn(n, names)
        self.assertEqual(img.elf.machine, "ARM")
        self.assertEqual(img.elf.entry, 0x08000001)
        self.assertEqual({s.name for s in img.elf.symbols}, {"main", "g_var"})
        # .data is loaded at its LMA in flash, not its RAM VMA
        self.assertEqual(img.byte_at(0x08002000), 0x11)
        self.assertIsNone(img.byte_at(0x20000000))
        self.assertIsNotNone(img.file_offset_of(0x08000000))
        self.assertIn("Compiler (.comment)", img.elf.build_info)

    def test_vaddr_mode(self):
        p = self.write("fw.elf", sample_elf(b"\x00" * 8))
        img = load_firmware(p, elf_use_vaddr=True)
        self.assertEqual(img.byte_at(0x20000000), 0x11)

    def test_section_and_symbol_comparison(self):
        a = parse_elf_bytes(sample_elf(b"\x00" * 32))
        b = parse_elf_bytes(sample_elf(b"\x00" * 30 + b"\x01\x02", bss_size=0x200, extra_symbol=True))
        cmp = compare_elf(a, b)
        by_name = {s.name: s for s in cmp.sections}
        self.assertEqual(by_name[".text"].status, "CHANGED")
        self.assertEqual(by_name[".text"].diff_bytes, 2)
        self.assertEqual(by_name[".rodata"].status, "SAME")
        self.assertEqual(by_name[".bss"].status, "CHANGED")
        self.assertEqual({s.name: s.status for s in cmp.symbols}, {"new_func": "ADDED"})

    def test_truncated_and_invalid_elf(self):
        good = sample_elf(b"\x00" * 8)
        with self.assertRaises(FirmwareFormatError):
            parse_elf_bytes(good[:40])
        with self.assertRaises(FirmwareFormatError):
            parse_elf_bytes(good[:len(good) // 2])
        with self.assertRaises(FirmwareFormatError):
            load_firmware(self.write("not.elf", b"this is not an elf file"))
        with self.assertRaises(FirmwareFormatError):
            parse_elf_bytes(b"\x7fELF\x03\x01" + bytes(60))


if __name__ == "__main__":
    unittest.main()
