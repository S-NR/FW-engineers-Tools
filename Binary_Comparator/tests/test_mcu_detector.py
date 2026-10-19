"""Heuristic controller identification."""

import os
import struct
import unittest

from firmware_binary_tool.core.binary_reader import identify_with_symbols, load_firmware
from firmware_binary_tool.core.mcu_detector import HIGH, LOW, MEDIUM, detect_mcu
from firmware_binary_tool.formats.elf_builder import TEXT, SectionSpec, build_elf32
from firmware_binary_tool.formats.hex_parser import write_intel_hex

from .helpers import TempDirTestCase, image_from_bytes


def cortex_m_image(sp=0x20020000, irqs=86, base=0x08000000) -> bytes:
    """Vector table (SP, reset, 15 exceptions, *irqs* interrupts) followed by code."""
    handler = (base + 0x200) | 1  # inside the code that follows the table
    vectors = [sp, handler] + [handler] * 14 + [handler] * irqs
    vectors[7:11] = [0, 0, 0, 0]  # reserved exception slots
    return struct.pack(f"<{len(vectors)}I", *vectors) + b"\x00" * 64 + b"\x70\x47" * 256  # zero padding + code


class StructuralTests(TempDirTestCase):
    def test_cortex_m_vector_table_on_raw_binary(self):
        img = image_from_bytes(cortex_m_image(), base=0x08000000)
        m = detect_mcu(img)
        self.assertEqual(m.core, "ARM Cortex-M")
        self.assertIn("STM32", m.family)
        self.assertEqual(m.confidence, LOW)
        self.assertEqual(m.details["Vector table"], "102 entries (86 IRQs)")  # trailing padding not counted
        self.assertEqual(m.details["RAM (from initial stack pointer)"], "≥ 128 KB at 0x20000000")

    def test_application_behind_bootloader(self):
        m = detect_mcu(image_from_bytes(cortex_m_image(base=0x08040000), base=0x08040000))
        self.assertIn("256 KB into flash", m.details["Image start"])

    def test_pic32mx_config_words_in_hex(self):
        p = self.path("pic.hex")
        write_intel_hex([(0x1D000000, b"\x00" * 0x100), (0x1FC00000, b"\x11" * 0x10),
                         (0x1FC02FF0, b"\xff" * 16)], p)
        m = detect_mcu(load_firmware(p))
        self.assertEqual(m.family, "PIC32MX3xx-7xx")
        self.assertEqual(m.core, "MIPS32")
        self.assertEqual(m.confidence, MEDIUM)

    def test_unknown(self):
        m = detect_mcu(image_from_bytes(os.urandom(256), base=0x60000000))
        self.assertFalse(m.known)
        self.assertIn("unknown", m.short())


class NamedEvidenceTests(TempDirTestCase):
    def test_stm32_from_elf_debug_strings(self):
        comment = ("GNU C17 11.3.1 -mcpu=cortex-m4 -mthumb -mfpu=fpv4-sp-d16\0"
                   "../Core/Startup/startup_stm32f411retx.s\0Drivers/CMSIS/Device/ST/STM32F4xx/Include/stm32f411xe.h"
                   "\0STM32CubeIDE")
        elf = build_elf32([SectionSpec(".text", 0x08000000, cortex_m_image(), flags=TEXT)], [], comment=comment)
        img = load_firmware(self.write("fw.elf", elf))
        m = img.mcu  # identified during load
        self.assertEqual(m.device, "STM32F411RET")
        self.assertEqual(m.family, "STM32F4 (STM32F411xE)")
        self.assertEqual(m.core, "ARM Cortex-M4 (FPU: fpv4-sp-d16)")
        self.assertEqual(m.confidence, HIGH)
        self.assertEqual(m.details["Flash (from part number)"], "512 KB")
        self.assertEqual(m.details["Pins (from part number)"], "64")
        self.assertEqual(m.details["Package (from part number)"], "LQFP")

    def test_pic32_from_map_file(self):
        p = self.path("pic.hex")
        write_intel_hex([(0x1D000000, b"\x00" * 0x100), (0x1FC02FF0, b"\xff" * 16)], p)
        img = load_firmware(p)
        self.assertNotEqual(img.mcu.device, "PIC32MX575F512L")
        mp = self.write("app.map", "Microchip PIC32 Memory-Usage Report\nprocessor: 32MX575F512L\n")
        identify_with_symbols(img, mp)
        m = img.mcu
        self.assertEqual(m.device, "PIC32MX575F512L")
        self.assertEqual(m.core, "MIPS32 M4K")
        self.assertEqual(m.details["Program flash (from part number)"], "512 KB")
        self.assertEqual(m.details["Pins (from part number)"], "100")
        self.assertEqual(m.confidence, HIGH)

    def test_family_placeholder_is_not_a_part_number(self):
        img = image_from_bytes(b"stm32f4xx stm32f407xx\0" + b"\x00" * 32, base=0x08000000)
        m = detect_mcu(img)
        self.assertIsNone(m.device)
        self.assertEqual(m.family, "STM32F4 (STM32F407xx)")

    def test_mismatch(self):
        stm = detect_mcu(image_from_bytes(cortex_m_image(), base=0x08000000))
        p = self.path("pic.hex")
        write_intel_hex([(0x1D000000, b"\x00" * 0x100), (0x1FC02FF0, b"\xff" * 16)], p)
        pic = detect_mcu(load_firmware(p))
        self.assertIs(stm.same_target_as(pic), False)
        self.assertIs(stm.same_target_as(stm), True)
        # an exact part inside a family-level result is compatible, not a mismatch
        mp = self.write("app.map", "processor 32MX575F512L\n")
        exact = load_firmware(p)
        identify_with_symbols(exact, mp)
        self.assertIsNone(exact.mcu.same_target_as(pic))
        other = load_firmware(p)
        identify_with_symbols(other, self.write("b.map", "processor 32MX150F128B\n"))
        self.assertIs(other.mcu.same_target_as(exact.mcu), False)


if __name__ == "__main__":
    unittest.main()
