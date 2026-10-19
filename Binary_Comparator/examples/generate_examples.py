"""Generate deterministic example firmware images for trying out the tool.

    python examples/generate_examples.py [output_dir]

Creates (default output: examples/firmware/):

    FW_V1.2.3.bin / FW_V1.2.4.bin      768 KB raw images at 0x08000000
    FW_V1.2.3.hex / FW_V1.2.4.hex      same content as Intel HEX
    FW_V1.2.3.elf / FW_V1.2.4.elf      ELF32 ARM files with sections and symbols
    FW_V1.2.3.map / FW_V1.2.4.map      GNU ld linker map files (functions + source modules)
    FW_V1.2.4_truncated.bin            512 KB image (size-mismatch demo)
    empty.bin                          0-byte file
    malformed.hex                      Intel HEX with a checksum error

Layout (matches examples/project_stm32.json):

    Bootloader   0x08000000 - 0x08007FFF   (version/build info at 0x08001000)
    Application  0x08008000 - 0x0803FFFF
    Calibration  0x08040000 - 0x0807FFFF   (CRC-32 in the last 4 bytes)
    Diagnostics  0x08080000 - 0x080BFFFF
"""

from __future__ import annotations

import os
import random
import sys
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from firmware_binary_tool.formats.elf_builder import RODATA, RWDATA, TEXT, SectionSpec, build_elf32  # noqa: E402
from firmware_binary_tool.formats.hex_parser import write_intel_hex  # noqa: E402

BASE = 0x08000000
SIZE = 0xC0000  # 768 KB


def _put(img: bytearray, addr: int, data: bytes) -> None:
    off = addr - BASE
    img[off:off + len(data)] = data


def _field(text: str, size: int) -> bytes:
    raw = text.encode("ascii")
    return raw[:size].ljust(size, b"\0")


def build_image(version: str, build_date: str, build_time: str, git: str, new: bool) -> bytearray:
    rnd = random.Random(2024)  # same "code" for both versions
    img = bytearray(b"\xFF" * SIZE)
    # Bootloader: 24 KB of code
    _put(img, BASE, bytes(rnd.getrandbits(8) for _ in range(0x6000)))
    # Version / build info block (expected to change between builds)
    _put(img, 0x08001000, _field(f"FW_VERSION={version}", 0x20))
    _put(img, 0x08001020, _field(f"Build: {build_date} {build_time}", 0x20))
    _put(img, 0x08001040, _field(f"git:{git}", 0x20))
    _put(img, 0x08001060, _field("Product: XC-200 Motor Controller", 0x30))
    _put(img, 0x08001090, _field("Board: MCB-REV-C", 0x20))
    # Application: 200 KB of code
    _put(img, 0x08008000, bytes(rnd.getrandbits(8) for _ in range(200 * 1024)))
    # Calibration tables
    _put(img, 0x08040000, bytes((i * 7) & 0xFF for i in range(0x8000)))
    # Diagnostics
    _put(img, 0x08080000, bytes(rnd.getrandbits(8) for _ in range(0x10000)))

    if new:
        patch = random.Random(99)
        # A 12 KB rewritten function block
        _put(img, 0x08012A40, bytes(patch.getrandbits(8) for _ in range(12352)))
        # Small scattered patches in the application
        for addr in (0x08009104, 0x0800F3A0, 0x08021000, 0x0802A7F2, 0x08033330):
            _put(img, addr, bytes(patch.getrandbits(8) for _ in range(patch.randint(2, 64))))
        # Application grew by 1 KB into previously erased flash
        _put(img, 0x0803A000, bytes(patch.getrandbits(8) for _ in range(1024)))
        # Diagnostics: a few changed bytes
        for addr in (0x08080100, 0x08084000, 0x08088888):
            _put(img, addr, bytes(patch.getrandbits(8) for _ in range(patch.randint(100, 300))))

    # CRC-32 over 0x08000000..0x0807FFFB stored little-endian at 0x0807FFFC
    crc = zlib.crc32(img[:0x7FFFC]) & 0xFFFFFFFF
    _put(img, 0x0807FFFC, crc.to_bytes(4, "little"))
    return img


# (name, address (VMA), size, kind, ELF section, source module). Thumb bit is added for the ELF.
def symbol_layout(new: bool) -> list[tuple[str, int, int, str, str, str]]:
    syms = [
        ("g_pfnVectors", 0x08000000, 0x400, "OBJECT", ".isr_vector", "startup.o"),
        ("Reset_Handler", 0x08000400, 0x80, "FUNC", ".boot", "startup.o"),
        ("bootloader_main", 0x08000480, 0xB80, "FUNC", ".boot", "boot.o"),
        ("fw_version", 0x08001000, 0x20, "OBJECT", ".boot", "version.o"),
        ("fw_build_timestamp", 0x08001020, 0x20, "OBJECT", ".boot", "version.o"),
        ("fw_git_hash", 0x08001040, 0x20, "OBJECT", ".boot", "version.o"),
        ("fw_product_info", 0x08001060, 0x50, "OBJECT", ".boot", "version.o"),
        ("boot_crypto_verify", 0x08001100, 0x4F00, "FUNC", ".boot", "boot.o"),
        ("main", 0x08008100, 0x240, "FUNC", ".text", "main.o"),
        ("adc_sample_task", 0x08009000, 0x400, "FUNC", ".text", "adc.o"),
        ("uart_protocol_parse", 0x0800F000, 0x800, "FUNC", ".text", "uart.o"),
        ("motor_control_loop", 0x08012A40, 0x3040 if new else 0x2F00, "FUNC", ".text", "motor.o"),
        ("motor_pwm_update", 0x08016000, 0x600, "FUNC", ".text", "motor.o"),
        ("pid_update", 0x08021040 if new else 0x08021000, 0x120, "FUNC", ".text", "pid.o"),
        ("can_rx_isr", 0x0802A7F0, 0x90, "FUNC", ".text", "can.o"),
        ("can_tx_queue", 0x0802B000, 0x300, "FUNC", ".text", "can.o"),
        ("g_config", 0x20000000, 0x40, "OBJECT", ".data", "config.o"),
        ("g_state", 0x20000200, 0x40, "OBJECT", ".bss", "main.o"),
        ("g_calibration", 0x08040000, 0x3FFFC, "OBJECT", ".calibration", "calib.o"),
        ("image_crc", 0x0807FFFC, 4, "OBJECT", ".calibration", "crc.o"),
        ("diag_ram_test", 0x08080000, 0x200, "FUNC", ".diagnostics", "diag.o"),
        ("diag_flash_test", 0x08084000, 0x400, "FUNC", ".diagnostics", "diag.o"),
        ("diag_watchdog_test", 0x08088800, 0x200, "FUNC", ".diagnostics", "diag.o"),
    ]
    if new:
        syms.append(("fault_logger_v2", 0x0803A000, 0x400, "FUNC", ".text", "fault.o"))
        syms.append(("g_motor_state_ext", 0x20002200, 0x400, "OBJECT", ".bss", "motor.o"))
    else:
        syms.append(("fault_logger", 0x0803A000, 0x200, "FUNC", ".text", "fault.o"))
    return syms


def write_gnu_map(path: str, new: bool) -> None:
    """Write a GNU ld style map file (-Wl,-Map=...) for the example build."""
    text_size = 200 * 1024 + (1024 if new else 0)
    out_sections = [
        (".isr_vector", 0x08000000, 0x400, None), (".boot", 0x08000400, 0x5C00, None),
        (".text", 0x08008000, text_size, None), (".rodata", 0x0803B000, 0x1000, None),
        (".data", 0x20000000, 0x200, 0x0803C000), (".bss", 0x20000200, 0x2400 if new else 0x2000, None),
        (".calibration", 0x08040000, 0x40000, None), (".diagnostics", 0x08080000, 0x10000, None),
    ]
    syms = symbol_layout(new)
    lines = ["Archive member included to satisfy reference by file (symbol)", "", "Discarded input sections", "",
             " .text.unused_helper", "                0x00000000       0x24 build/util.o", "",
             "Memory Configuration", "", "Name             Origin             Length             Attributes",
             "FLASH            0x08000000         0x00100000         xr",
             "RAM              0x20000000         0x00020000         xrw", "", "Linker script and memory map", "",
             "LOAD build/startup.o", ""]
    for name, vma, size, lma in out_sections:
        load = f" load address 0x{lma:08x}" if lma is not None else ""
        lines.append(f"{name:<15} 0x{vma:08x} {size:#10x}{load}")
        if name == ".text":
            lines.append(" *(.text*)")
        for sname, addr, ssize, kind, sec, module in sorted(syms, key=lambda x: x[1]):
            if sec != name:
                continue
            if kind == "FUNC":  # -ffunction-sections naming, placed by the linker script
                in_sec = f".text.{sname}"
            else:
                in_sec = {".data": ".data", ".bss": ".bss"}.get(sec, ".rodata") + f".{sname}"
            if len(in_sec) >= 15:
                lines.append(f" {in_sec}")
                lines.append(f"{'':16}0x{addr:08x} {ssize:#10x} build/{module}")
            else:
                lines.append(f" {in_sec:<14} 0x{addr:08x} {ssize:#10x} build/{module}")
            lines.append(f"{'':16}0x{addr:08x}                {sname}")
        if name == ".bss":
            lines.append(f"{'':16}0x{vma + size:08x}                _ebss = .")
        lines.append("")
    with open(path, "w", encoding="ascii", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


def build_elf(img: bytearray, new: bool) -> bytes:
    def sl(a, n):
        return bytes(img[a - BASE:a - BASE + n])

    text_size = 200 * 1024 if not new else 200 * 1024 + 1024
    sections = [
        SectionSpec(".isr_vector", 0x08000000, sl(0x08000000, 0x400), flags=RODATA),
        SectionSpec(".boot", 0x08000400, sl(0x08000400, 0x5C00), flags=TEXT),
        SectionSpec(".text", 0x08008000, sl(0x08008000, text_size), flags=TEXT),
        SectionSpec(".rodata", 0x0803B000, sl(0x0803B000, 0x1000), flags=RODATA),
        SectionSpec(".data", 0x20000000, sl(0x0803C000, 0x200), flags=RWDATA, lma=0x0803C000),
        SectionSpec(".bss", 0x20000200, None, size=0x2000 if not new else 0x2400, flags=RWDATA),
        SectionSpec(".calibration", 0x08040000, sl(0x08040000, 0x40000), flags=RODATA),
        SectionSpec(".diagnostics", 0x08080000, sl(0x08080000, 0x10000), flags=TEXT),
    ]
    symbols = [(n, a | 1 if k == "FUNC" else a, size, k, sec) for n, a, size, k, sec, _ in symbol_layout(new)]
    return build_elf32(sections, symbols, entry=0x08000401,
                       comment="GCC: (Arm GNU Toolchain 12.2.Rel1) 12.2.1 20221205")


def main(out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    old = build_image("1.2.3", "Jan 12 2026", "10:22:33", "3f9a2c1", new=False)
    new = build_image("1.2.4", "Feb 03 2026", "16:05:10", "8c41d7e", new=True)

    def out(name):
        return os.path.join(out_dir, name)

    for name, img in (("FW_V1.2.3", old), ("FW_V1.2.4", new)):
        with open(out(name + ".bin"), "wb") as f:
            f.write(img)
        # HEX: omit erased (0xFF) 4 KB pages to produce a realistic sparse image
        segs = []
        for off in range(0, SIZE, 0x1000):
            page = bytes(img[off:off + 0x1000])
            if page != b"\xFF" * 0x1000:
                segs.append((BASE + off, page))
        write_intel_hex(segs, out(name + ".hex"), start_linear=0x08000401)
        with open(out(name + ".elf"), "wb") as f:
            f.write(build_elf(img, name.endswith("1.2.4")))
        write_gnu_map(out(name + ".map"), name.endswith("1.2.4"))
    with open(out("FW_V1.2.4_truncated.bin"), "wb") as f:
        f.write(new[:0x80000])
    with open(out("empty.bin"), "wb"):
        pass
    with open(out("malformed.hex"), "w", encoding="ascii") as f:
        f.write(":020000040800F2\n:10000000FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF01\n:00000001FF\n")
    print(f"Example firmware written to {os.path.abspath(out_dir)}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "firmware"))
