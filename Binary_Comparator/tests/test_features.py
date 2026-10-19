"""Symbol loading (ELF + GNU ld map) and function/module-level change analysis."""

import contextlib
import io
import json
import unittest

from firmware_binary_tool.cli.cli import EXIT_DIFF, run
from firmware_binary_tool.core.feature_diff import ADDED, MODIFIED, MOVED, NOT_IN_IMAGE, RELOCATED, REMOVED, UNCHANGED
from firmware_binary_tool.core.region_manager import MemoryRegion, ProjectConfig
from firmware_binary_tool.core.session import load_and_compare
from firmware_binary_tool.core.symbols import load_symbol_table, parse_gnu_map
from firmware_binary_tool.errors import FirmwareFormatError
from firmware_binary_tool.formats.elf_builder import RWDATA, TEXT, SectionSpec, build_elf32

from .helpers import TempDirTestCase

BASE = 0x08000000

MAP_TEMPLATE = """Archive member included to satisfy reference by file (symbol)

Discarded input sections

 .text.unused   0x00000000       0x10 build/unused.o

Memory Configuration

Name             Origin             Length             Attributes
FLASH            0x08000000         0x00010000         xr

Linker script and memory map

LOAD build/main.o

.text           0x08000000     0x1000
 *(.text*)
 .text.main     0x08000000      0x100 build/main.o
                0x08000000                main
 .text.motor_control_loop
                0x08000100      {motor_size} build/motor.o
                0x08000100                motor_control_loop
 .text          0x08000400      0x200 lib/libdrv.a(pwm.o)
                0x08000400                pwm_init
                0x08000480                pwm_set_duty
 *fill*         0x08000600      0x200
 .text.{extra}
                0x08000800      0x100 build/{extra}.o
                0x08000800                {extra}

.data           0x20000000       0x10 load address 0x08001000
 .data.g_config
                0x20000000       0x10 build/config.o
                0x20000000                g_config
                0x20000010                _edata = .

.bss            0x20000010       0x40
 COMMON         0x20000010       0x40 build/main.o
                0x20000010       0x40 g_buffer
"""


def map_text(motor_size="0x200", extra="helper_old"):
    return MAP_TEMPLATE.format(motor_size=motor_size, extra=extra)


def make_image(changes=None) -> bytes:
    img = bytearray(i & 0xFF for i in range(0x1100))
    for off, val in (changes or {}).items():
        img[off] = val
    return bytes(img)


class MapParserTests(TempDirTestCase):
    def test_parse_gnu_map(self):
        t = parse_gnu_map(self.write("a.map", map_text()))
        by = {s.name: s for s in t.symbols}
        self.assertEqual(set(by), {"main", "motor_control_loop", "pwm_init", "pwm_set_duty", "helper_old",
                                   "g_config", "g_buffer"})
        self.assertEqual((by["main"].load_address, by["main"].size, by["main"].module), (BASE, 0x100, "main.o"))
        self.assertEqual(by["motor_control_loop"].size, 0x200)
        # sizes of unsized symbols come from the next symbol / end of input section; archive member names
        self.assertEqual((by["pwm_init"].size, by["pwm_set_duty"].size), (0x80, 0x180))
        self.assertEqual(by["pwm_init"].module, "pwm.o")
        self.assertEqual(by["main"].kind, "FUNC")
        # .data: VMA in RAM, LMA in flash
        self.assertEqual((by["g_config"].address, by["g_config"].load_address), (0x20000000, 0x08001000))
        self.assertEqual(by["g_config"].kind, "OBJECT")
        self.assertEqual(by["g_buffer"].size, 0x40)
        self.assertEqual(t.symbol_at(0x08000105).name, "motor_control_loop")
        self.assertIsNone(t.symbol_at(0x08000700))  # *fill*

    def test_not_a_map(self):
        with self.assertRaises(FirmwareFormatError):
            parse_gnu_map(self.write("x.map", "hello\nworld\n"))
        with self.assertRaises(FirmwareFormatError):
            load_symbol_table(self.path("missing.map"))


class FeatureDiffTests(TempDirTestCase):
    def test_bin_with_map_files(self):
        old = make_image()
        new = make_image({0x150: 0xAA, 0x151: 0xBB, 0x1000: 0x99})  # motor_control_loop + g_config initial value
        pa, pb = self.write("a.bin", old), self.write("b.bin", new)
        ma = self.write("a.map", map_text())
        mb = self.write("b.map", map_text(motor_size="0x280", extra="helper_new"))
        cfg = ProjectConfig(base_address=BASE)
        s = load_and_compare(pa, pb, cfg, symbols_a=ma, symbols_b=mb)
        try:
            st = {c.name: c for c in s.features.symbols}
            self.assertEqual(st["motor_control_loop"].status, MODIFIED)
            self.assertEqual(st["motor_control_loop"].changed_bytes, 2 + 0x80)  # 2 bytes + growth
            self.assertEqual(st["g_config"].status, MODIFIED)  # found via its flash LMA
            self.assertEqual(st["main"].status, UNCHANGED)
            self.assertEqual(st["helper_new"].status, ADDED)
            self.assertEqual(st["helper_old"].status, REMOVED)
            self.assertEqual(st["g_buffer"].status, NOT_IN_IMAGE)
            mods = {m.module: m for m in s.features.modules}
            self.assertEqual(mods["motor.o"].modified, 1)
            self.assertEqual(mods["config.o"].changed_bytes, 1)
            self.assertNotIn("main.o", mods)
            self.assertEqual(s.symbol_label(0x08000151), "motor_control_loop+0x51 (motor.o)")
        finally:
            s.close()

    def test_moved_function_is_not_modified(self):
        code = bytes(range(64))

        def elf(func_addr, prefix):
            text = prefix + code
            return build_elf32([SectionSpec(".text", BASE, text, flags=TEXT),
                                SectionSpec(".bss", 0x20000000, None, size=0x10, flags=RWDATA)],
                               [("pre", BASE | 1, len(prefix), "FUNC", ".text"),
                                ("algo", func_addr | 1, 64, "FUNC", ".text")])

        pa = self.write("a.elf", elf(BASE + 0x10, b"\x01" * 0x10))
        pb = self.write("b.elf", elf(BASE + 0x20, b"\x02" * 0x20))
        s = load_and_compare(pa, pb)
        try:
            st = {c.name: c for c in s.features.symbols}
            self.assertEqual(st["algo"].status, MOVED)  # same bytes, new address
            self.assertEqual(st["pre"].status, MODIFIED)
            self.assertEqual((st["algo"].addr_a, st["algo"].addr_b), (BASE + 0x10, BASE + 0x20))  # Thumb bit cleared
        finally:
            s.close()

    def test_removed_function_does_not_mark_unrelated_callers_modified(self):
        """Commenting out one call (e.g. watchdog init) shifts later code. Callers / pointer tables of the
        shifted code get new branch offsets and literal values; they must be RELOCATED, not MODIFIED."""
        nop = b"\x00\xbf"

        def bl(pc, target):  # Thumb-2 BL encoding
            off = (target - (pc + 4)) & 0x1FFFFFF
            s, i1, i2 = (off >> 24) & 1, (off >> 23) & 1, (off >> 22) & 1
            j1, j2 = (1 ^ i1) ^ s, (1 ^ i2) ^ s
            hw1 = 0xF000 | (s << 10) | ((off >> 12) & 0x3FF)
            hw2 = 0xD000 | (j1 << 13) | (j2 << 11) | ((off >> 1) & 0x7FF)
            return hw1.to_bytes(2, "little") + hw2.to_bytes(2, "little")

        def build(with_watchdog: bool) -> bytes:
            init_size = 0x10 if with_watchdog else 0x0C
            f1 = BASE + init_size
            wdg = f1 + 0x10
            f3 = wdg + 0x10 if with_watchdog else f1 + 0x10
            rodata = BASE + 0x100
            string = rodata + (0 if with_watchdog else 4)  # string literal moves too (no symbol for it)
            init = nop + (bl(BASE + 2, wdg) if with_watchdog else b"") + bl(BASE + len(nop) + (4 if with_watchdog else 0), f3) + nop * 2
            init = init[:init_size].ljust(init_size, b"\x00")
            body1 = bl(f1, f3) + nop * 2 + (f3 | 1).to_bytes(4, "little") + string.to_bytes(4, "little")
            text = init + body1 + (b"\x11" * 0x10 if with_watchdog else b"") + b"\x22" * 0x10
            rod = (b"" if with_watchdog else b"\0" * 4) + b"Hello world\0"
            syms = [("peripheralInit", BASE | 1, init_size, "FUNC", ".text"),
                    ("sensor_task", f1 | 1, 0x10, "FUNC", ".text"),
                    ("crc_calc", f3 | 1, 0x10, "FUNC", ".text")]
            if with_watchdog:
                syms.append(("watchdog_init", wdg | 1, 0x10, "FUNC", ".text"))
            return build_elf32([SectionSpec(".text", BASE, text, flags=TEXT),
                                SectionSpec(".rodata", rodata, rod, flags=0x2)], syms)

        s = load_and_compare(self.write("a.elf", build(True)), self.write("b.elf", build(False)))
        try:
            st = {c.name: c for c in s.features.symbols}
            self.assertEqual(st["peripheralInit"].status, MODIFIED)  # the real edit
            self.assertEqual(st["watchdog_init"].status, REMOVED)
            self.assertEqual(st["crc_calc"].status, MOVED)  # identical code, new address
            self.assertEqual(st["sensor_task"].status, RELOCATED)  # only BL offset + pointers changed
            self.assertEqual(st["sensor_task"].changed_bytes, 0)
            self.assertGreater(st["sensor_task"].reloc_bytes, 0)
            self.assertLessEqual(st["sensor_task"].reloc_bytes, 12)  # BL (4) + function pointer (4) + string ptr (4)
            self.assertEqual([c.name for c in s.features.changed], ["peripheralInit", "watchdog_init"])
            self.assertEqual([m.module for m in s.features.modules], ["(unknown module)"])
        finally:
            s.close()

    def test_expected_only_symbol_and_one_sided_symbols(self):
        pa, pb = self.write("a.bin", make_image()), self.write("b.bin", make_image({0x10: 0}))
        cfg = ProjectConfig(base_address=BASE, expected=[MemoryRegion("ver", BASE + 0x10, BASE + 0x1F)])
        s = load_and_compare(pa, pb, cfg, symbols_b=self.write("b.map", map_text()))
        try:
            main = next(c for c in s.features.symbols if c.name == "main")
            self.assertTrue(main.expected_only)
            self.assertTrue(s.features.modules[0].expected_only)
            self.assertTrue(any("only one build" in n for n in s.features.notes))
        finally:
            s.close()

    def test_no_symbols(self):
        pa = self.write("a.bin", make_image())
        s = load_and_compare(pa, pa)
        try:
            self.assertIsNone(s.features)
            self.assertEqual(s.symbol_label(0), "")
        finally:
            s.close()

    def test_cli_functions_output(self):
        pa, pb = self.write("a.bin", make_image()), self.write("b.bin", make_image({0x150: 0xAA}))
        ma, js, fcsv = self.write("a.map", map_text()), self.path("s.json"), self.path("f.csv")
        out = io.StringIO()
        with contextlib.redirect_stderr(io.StringIO()):
            code = run([pa, pb, "--base", "0x08000000", "--symbols-a", ma, "--symbols-b", ma,
                        "--json", js, "--functions-csv", fcsv], out=out)
        text = out.getvalue()
        self.assertEqual(code, EXIT_DIFF)
        self.assertIn("Changed functions / variables", text)
        self.assertIn("motor_control_loop", text)
        self.assertIn("motor.o", text)
        with open(js, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual([x["name"] for x in data["features"]["symbols"]], ["motor_control_loop"])
        with open(fcsv, encoding="utf-8") as f:
            self.assertIn("motor_control_loop", f.read())


MIPS_TEXT = 0x9D000000  # PIC32 KSEG0 program flash (physical 0x1D000000)
MIPS_RAM = 0xA0000000  # KSEG1 RAM


def mips_word(w: int) -> bytes:
    return w.to_bytes(4, "little")


def mips_build(shift: int, extra_name: str = "C.5.100") -> bytes:
    """ELF32 MIPS (PIC32 style): 'user' calls 'helper' (JAL) and builds &counter with LUI/ADDIU.

    *shift* inserts bytes before 'helper' and 'counter' so they move, without editing 'user'.
    """
    user = MIPS_TEXT
    helper = MIPS_TEXT + 0x20 + shift
    counter = MIPS_RAM + 0x10 + shift
    hi, lo = (counter + 0x8000) >> 16, counter & 0xFFFF
    code = b"".join(mips_word(w) for w in (
        0x0C000000 | ((helper >> 2) & 0x3FFFFFF),  # jal helper
        0x00000000,  # nop (delay slot)
        0x3C040000 | hi,  # lui $a0, %hi(counter)
        0x24840000 | lo,  # addiu $a0, $a0, %lo(counter)
        0x03E00008,  # jr $ra
        0x00000000,
        0, 0))
    text = code + b"\x00" * shift + b"\x11" * 0x10
    rodata = b"const-table-data"
    return build_elf32(
        [SectionSpec(".text", MIPS_TEXT, text, flags=TEXT),
         SectionSpec(".rodata", MIPS_TEXT + 0x1000, rodata, flags=0x2),
         SectionSpec(".bss", MIPS_RAM, None, size=0x100, flags=RWDATA)],
        [("user", user, 0x18, "FUNC", ".text"), ("helper", helper, 0x10, "FUNC", ".text"),
         ("counter", counter, 4, "OBJECT", ".bss"), (extra_name, MIPS_TEXT + 0x1000, 16, "OBJECT", ".rodata"),
         ("_gp", MIPS_RAM + 0x8000, 0, "NOTYPE", ".bss")],
        entry=user, machine=8)


class ArchitectureTests(TempDirTestCase):
    def test_pic32_kseg_addresses_match_hex(self):
        """PIC32 ELF uses KSEG0 addresses, HEX files physical ones; both must line up."""
        from firmware_binary_tool.formats.hex_parser import write_intel_hex
        elf = self.write("a.elf", mips_build(0))
        img = load_firmware_for_test(elf)
        self.assertEqual(img.segments[0].start, 0x1D000000)  # converted to physical
        hexp = self.path("a.hex")
        write_intel_hex([(s.start, bytes(s.data)) for s in img.segments], hexp)
        t = load_symbol_table(elf)
        user = next(s for s in t.symbols if s.name == "user")
        self.assertEqual((user.address, user.load_address), (MIPS_TEXT, 0x1D000000))
        # HEX + ELF symbols: functions are found in the HEX image
        s = load_and_compare(hexp, hexp, symbols_a=elf, symbols_b=elf)
        try:
            self.assertEqual(s.features.unattributed_bytes, 0)
            self.assertTrue(all(c.status in (UNCHANGED, NOT_IN_IMAGE) for c in s.features.symbols))
        finally:
            s.close()

    def test_mips_moved_callee_and_variable_is_relocated(self):
        s = load_and_compare(self.write("a.elf", mips_build(0)), self.write("b.elf", mips_build(0x20, "C.5.200")))
        try:
            st = {c.name: c for c in s.features.symbols}
            self.assertEqual(st["user"].status, RELOCATED)  # JAL target + LUI/ADDIU changed only
            self.assertEqual(st["user"].changed_bytes, 0)
            self.assertEqual(st["helper"].status, MOVED)
            # compiler-generated constant renamed C.5.100 -> C.5.200 with identical content: not ADDED/REMOVED
            self.assertEqual(s.features.changed, [])
            self.assertIn("C.5.200", st)
            self.assertIn("compiler-generated name", " ".join(st["C.5.200"].notes))
        finally:
            s.close()

    def test_pointer_to_linker_label_and_aliases(self):
        """Pointers to size-0 linker labels (_end) and functions with alias names are address-only changes."""
        def build(shift: int, alias: str) -> bytes:
            f = BASE + 0x40 + shift
            end = 0x20000100 + shift
            user = (f | 1).to_bytes(4, "little") + end.to_bytes(4, "little")
            text = user + b"\x00" * (0x38 + shift) + b"\x22" * 0x10
            return build_elf32([SectionSpec(".text", BASE, text, flags=TEXT),
                                SectionSpec(".bss", 0x20000000, None, size=0x200, flags=RWDATA)],
                               [("ptrs", BASE, 8, "OBJECT", ".text"), ("worker", f | 1, 0x10, "FUNC", ".text"),
                                (alias, f | 1, 0x10, "FUNC", ".text"), ("_end", end, 0, "NOTYPE", ".bss")])

        s = load_and_compare(self.write("a.elf", build(0, "worker_v1")), self.write("b.elf", build(8, "worker_alias2")))
        try:
            st = {c.name: c for c in s.features.symbols}
            self.assertEqual(st["ptrs"].status, RELOCATED)
            self.assertEqual(s.features.changed, [])  # aliases do not show up as ADDED / REMOVED
        finally:
            s.close()


def load_firmware_for_test(path):
    from firmware_binary_tool.core.binary_reader import load_firmware
    return load_firmware(path)


if __name__ == "__main__":
    unittest.main()
