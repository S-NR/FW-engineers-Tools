"""Minimal, dependency-free ELF reader (32/64-bit, little/big endian).

Extracts what a firmware engineer needs for comparison:

* program headers (PT_LOAD segments -> the bytes that get flashed)
* section headers (.text, .rodata, .data, .bss, custom sections ...)
* symbol table (.symtab / .dynsym)
* build information (.comment compiler strings, GNU build-id)

Every table access is bounds-checked; a truncated or corrupt ELF raises
:class:`FirmwareFormatError` instead of crashing.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from ..core.image import Segment
from ..errors import FirmwareFormatError

PT_LOAD = 1
SHT_NAMES = {
    0: "NULL", 1: "PROGBITS", 2: "SYMTAB", 3: "STRTAB", 4: "RELA", 5: "HASH", 6: "DYNAMIC",
    7: "NOTE", 8: "NOBITS", 9: "REL", 11: "DYNSYM", 14: "INIT_ARRAY", 15: "FINI_ARRAY",
    16: "PREINIT_ARRAY", 17: "GROUP", 18: "SYMTAB_SHNDX", 0x70000001: "ARM_EXIDX",
    0x70000003: "ARM_ATTRIBUTES",
}
SHT_SYMTAB, SHT_NOBITS, SHT_DYNSYM, SHT_NOTE = 2, 8, 11, 7
SHF_WRITE, SHF_ALLOC, SHF_EXECINSTR = 0x1, 0x2, 0x4
MACHINES = {
    0: "None", 2: "SPARC", 3: "x86", 8: "MIPS", 20: "PowerPC", 21: "PowerPC64", 40: "ARM",
    42: "SuperH", 50: "IA-64", 62: "x86-64", 83: "AVR", 87: "V850", 88: "M32R", 92: "OpenRISC",
    94: "Xtensa", 105: "MSP430", 106: "Blackfin", 113: "Nios II", 164: "Hexagon", 183: "AArch64",
    191: "TI C2000", 220: "Z80", 243: "RISC-V", 247: "BPF", 258: "LoongArch", 0x5441: "Fujitsu FR",
}
FILE_TYPES = {0: "NONE", 1: "REL (relocatable)", 2: "EXEC (executable)", 3: "DYN (shared)", 4: "CORE"}
SYM_TYPES = {0: "NOTYPE", 1: "OBJECT", 2: "FUNC", 3: "SECTION", 4: "FILE", 5: "COMMON", 6: "TLS"}
SYM_BINDS = {0: "LOCAL", 1: "GLOBAL", 2: "WEAK"}


@dataclass
class ElfSection:
    index: int
    name: str
    type: int
    flags: int
    addr: int
    offset: int
    size: int
    link: int = 0

    @property
    def type_name(self) -> str:
        return SHT_NAMES.get(self.type, f"0x{self.type:X}")

    @property
    def is_alloc(self) -> bool:
        return bool(self.flags & SHF_ALLOC)

    @property
    def is_nobits(self) -> bool:
        return self.type == SHT_NOBITS

    @property
    def flag_str(self) -> str:
        return "".join(c for c, bit in (("W", SHF_WRITE), ("A", SHF_ALLOC), ("X", SHF_EXECINSTR)) if self.flags & bit)


@dataclass
class ElfProgramHeader:
    type: int
    offset: int
    vaddr: int
    paddr: int
    filesz: int
    memsz: int
    flags: int


@dataclass
class ElfSymbol:
    name: str
    value: int
    size: int
    type: str
    bind: str
    section: str
    file: str = ""  # source file for LOCAL symbols (from the preceding STT_FILE symbol)


@dataclass
class ElfInfo:
    elf_class: int  # 32 or 64
    endian: str  # 'little' | 'big'
    machine: str
    file_type: str
    entry: int
    sections: list[ElfSection]
    program_headers: list[ElfProgramHeader]
    symbols: list[ElfSymbol]
    build_info: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    raw: bytes = field(default=b"", repr=False)

    def section_data(self, section: ElfSection) -> bytes:
        if section.is_nobits:
            return b""
        return self.raw[section.offset:section.offset + section.size]

    def summary(self) -> dict[str, str]:
        out = {
            "ELF class": f"ELF{self.elf_class}",
            "Endianness": self.endian,
            "Machine": self.machine,
            "Type": self.file_type,
            "Entry point": f"0x{self.entry:08X}",
            "Sections": str(len(self.sections)),
            "Symbols": str(len(self.symbols)),
        }
        out.update(self.build_info)
        return out


class _Reader:
    def __init__(self, raw: bytes, endian: str):
        self.raw = raw
        self.prefix = "<" if endian == "little" else ">"

    def unpack(self, fmt: str, offset: int, what: str) -> tuple:
        size = struct.calcsize(self.prefix + fmt)
        if offset < 0 or offset + size > len(self.raw):
            raise FirmwareFormatError(f"ELF truncated: {what} at offset 0x{offset:X} lies beyond end of file")
        return struct.unpack_from(self.prefix + fmt, self.raw, offset)


def _cstr(table: bytes, offset: int) -> str:
    if offset >= len(table):
        return f"<bad name @{offset}>"
    end = table.find(b"\0", offset)
    if end < 0:
        end = len(table)
    return table[offset:end].decode("utf-8", errors="replace")


def parse_elf(path: str) -> ElfInfo:
    with open(path, "rb") as f:
        raw = f.read()
    return parse_elf_bytes(raw)


def parse_elf_bytes(raw: bytes) -> ElfInfo:
    if len(raw) < 16 or raw[:4] != b"\x7fELF":
        raise FirmwareFormatError("Not an ELF file (missing \\x7fELF magic)")
    ei_class, ei_data = raw[4], raw[5]
    if ei_class not in (1, 2):
        raise FirmwareFormatError(f"Unsupported ELF class {ei_class}")
    if ei_data not in (1, 2):
        raise FirmwareFormatError(f"Unsupported ELF data encoding {ei_data}")
    is64 = ei_class == 2
    endian = "little" if ei_data == 1 else "big"
    r = _Reader(raw, endian)
    warnings: list[str] = []

    hdr_fmt = "HHIQQQIHHHHHH" if is64 else "HHIIIIIHHHHHH"
    (e_type, e_machine, _ver, e_entry, e_phoff, e_shoff, _flags, _ehsize,
     e_phentsize, e_phnum, e_shentsize, e_shnum, e_shstrndx) = r.unpack(hdr_fmt, 16, "ELF header")

    # ----------------------------------------------------------- section headers
    sh_fmt = "IIQQQQIIQQ" if is64 else "IIIIIIIIII"
    sh_size = struct.calcsize("<" + sh_fmt)
    sections: list[ElfSection] = []
    if e_shoff:
        if e_shentsize and e_shentsize < sh_size:
            raise FirmwareFormatError(f"ELF section header entry size {e_shentsize} is too small")
        entsize = e_shentsize or sh_size
        if e_shnum == 0:  # extended numbering: real count in section 0's sh_size
            e_shnum = r.unpack(sh_fmt, e_shoff, "section header 0")[5]
        if e_shnum > 65535 * 4:
            raise FirmwareFormatError(f"Implausible section count {e_shnum}")
        raw_sections = []
        for i in range(e_shnum):
            (name, stype, flags, addr, offset, size, link, _info, _align, _entsize) = r.unpack(
                sh_fmt, e_shoff + i * entsize, f"section header {i}")
            raw_sections.append((name, stype, flags, addr, offset, size, link))
        if e_shstrndx == 0xFFFF and raw_sections:
            e_shstrndx = raw_sections[0][6]
        strtab = b""
        if 0 < e_shstrndx < len(raw_sections):
            _, _, _, _, off, size, _ = raw_sections[e_shstrndx]
            strtab = raw[off:off + size]
        else:
            warnings.append("ELF has no section name string table; section names are unavailable.")
        for i, (name, stype, flags, addr, offset, size, link) in enumerate(raw_sections):
            sec = ElfSection(i, _cstr(strtab, name) if strtab else f"section{i}", stype, flags, addr, offset, size, link)
            if not sec.is_nobits and stype != 0 and offset + size > len(raw):
                raise FirmwareFormatError(
                    f"ELF section '{sec.name}' (offset 0x{offset:X}, size {size}) extends beyond end of file")
            sections.append(sec)
    else:
        warnings.append("ELF has no section headers.")

    # ----------------------------------------------------------- program headers
    ph_fmt = "IIQQQQQQ" if is64 else "IIIIIIII"
    ph_size = struct.calcsize("<" + ph_fmt)
    pheaders: list[ElfProgramHeader] = []
    if e_phoff and e_phnum:
        entsize = e_phentsize or ph_size
        if entsize < ph_size:
            raise FirmwareFormatError(f"ELF program header entry size {entsize} is too small")
        for i in range(e_phnum):
            v = r.unpack(ph_fmt, e_phoff + i * entsize, f"program header {i}")
            if is64:
                p_type, p_flags, p_offset, p_vaddr, p_paddr, p_filesz, p_memsz, _ = v
            else:
                p_type, p_offset, p_vaddr, p_paddr, p_filesz, p_memsz, p_flags, _ = v
            pheaders.append(ElfProgramHeader(p_type, p_offset, p_vaddr, p_paddr, p_filesz, p_memsz, p_flags))

    # ----------------------------------------------------------------- symbols
    symbols: list[ElfSymbol] = []
    sym_fmt = "IBBHQQ" if is64 else "IIIBBH"
    sym_size = struct.calcsize("<" + sym_fmt)
    symtabs = [s for s in sections if s.type == SHT_SYMTAB] or [s for s in sections if s.type == SHT_DYNSYM]
    for symsec in symtabs[:1]:
        names = b""
        if 0 < symsec.link < len(sections):
            ss = sections[symsec.link]
            names = raw[ss.offset:ss.offset + ss.size]
        count = symsec.size // sym_size
        current_file = ""
        for i in range(1, count):  # entry 0 is the null symbol
            v = r.unpack(sym_fmt, symsec.offset + i * sym_size, f"symbol {i}")
            if is64:
                st_name, st_info, _other, st_shndx, st_value, st_size = v
            else:
                st_name, st_value, st_size, st_info, _other, st_shndx = v
            stype = st_info & 0xF
            if stype == 4:  # FILE: names the source of the LOCAL symbols that follow
                current_file = _cstr(names, st_name) if names else ""
                continue
            if stype == 3:  # SECTION symbols are noise for comparison
                continue
            name = _cstr(names, st_name) if names else ""
            if not name:
                continue
            if st_shndx == 0:
                secname = "UND"
            elif st_shndx == 0xFFF1:
                secname = "ABS"
            elif st_shndx == 0xFFF2:
                secname = "COMMON"
            elif st_shndx < len(sections):
                secname = sections[st_shndx].name
            else:
                secname = f"#{st_shndx}"
            bind = st_info >> 4
            symbols.append(ElfSymbol(name, st_value, st_size, SYM_TYPES.get(stype, str(stype)),
                                     SYM_BINDS.get(bind, str(bind)), secname, current_file if bind == 0 else ""))

    info = ElfInfo(
        elf_class=64 if is64 else 32,
        endian=endian,
        machine=MACHINES.get(e_machine, f"machine {e_machine}"),
        file_type=FILE_TYPES.get(e_type, str(e_type)),
        entry=e_entry,
        sections=sections,
        program_headers=pheaders,
        symbols=symbols,
        warnings=warnings,
        raw=raw,
    )
    info.build_info = _build_info(info, r)
    return info


def _build_info(info: ElfInfo, r: _Reader) -> dict[str, str]:
    out: dict[str, str] = {}
    for sec in info.sections:
        if sec.name == ".comment" and not sec.is_nobits:
            parts = [p.decode("utf-8", "replace").strip() for p in info.section_data(sec).split(b"\0")]
            parts = [p for p in dict.fromkeys(parts) if p]
            if parts:
                out["Compiler (.comment)"] = "; ".join(parts)
        elif sec.type == SHT_NOTE and sec.name == ".note.gnu.build-id":
            try:
                namesz, descsz, _ntype = r.unpack("III", sec.offset, "build-id note")
                desc_off = sec.offset + 12 + ((namesz + 3) & ~3)
                desc = info.raw[desc_off:desc_off + descsz]
                if desc:
                    out["GNU build-id"] = desc.hex()
            except FirmwareFormatError:
                pass
    return out


def mips_kseg_to_phys(addr: int) -> int:
    """MIPS32 KSEG0 (0x80000000-0x9FFFFFFF) / KSEG1 (0xA0000000-0xBFFFFFFF) -> physical address."""
    return addr & 0x1FFFFFFF if 0x80000000 <= addr < 0xC0000000 else addr


def physical_translator(info: ElfInfo):
    """Function mapping ELF load addresses to the physical addresses used in HEX files / by programmers.

    PIC32 (MIPS32) ELF files use KSEG0/KSEG1 virtual addresses even for p_paddr, while the Intel HEX file
    and the programmer use physical addresses (e.g. 0x9D000000 -> 0x1D000000, 0xBFC00000 -> 0x1FC00000).
    """
    if info.machine == "MIPS" and info.elf_class == 32:
        return mips_kseg_to_phys
    return lambda a: a


def load_segments(info: ElfInfo, use_vaddr: bool = False) -> tuple[list[Segment], list[str]]:
    """Return the loadable (flash) content of the ELF as image segments.

    By default the load memory address (LMA / p_paddr) is used, because that is
    where bytes are programmed into flash. ``use_vaddr`` selects the VMA.
    """
    warnings: list[str] = []
    segs: list[Segment] = []
    to_phys = physical_translator(info)
    translated = False
    for ph in info.program_headers:
        if ph.type != PT_LOAD or ph.filesz == 0:
            continue
        if ph.offset + ph.filesz > len(info.raw):
            raise FirmwareFormatError(
                f"ELF PT_LOAD segment at offset 0x{ph.offset:X} (size {ph.filesz}) extends beyond end of file")
        addr = ph.vaddr if use_vaddr else to_phys(ph.paddr)
        translated |= addr != (ph.vaddr if use_vaddr else ph.paddr)
        segs.append(Segment(addr, info.raw[ph.offset:ph.offset + ph.filesz], ph.offset,
                            f"PT_LOAD @0x{addr:08X}"))
        if ph.memsz > ph.filesz:
            warnings.append(
                f"PT_LOAD @0x{addr:08X}: {ph.memsz - ph.filesz} bytes of zero-initialised memory (.bss) "
                "are not part of the flash image and are not compared byte-by-byte.")
    if translated:
        warnings.append("MIPS KSEG0/KSEG1 load addresses were converted to physical addresses "
                        "(as used in Intel HEX files and by programmers), e.g. 0x9D000000 -> 0x1D000000.")
    if not segs:
        for sec in info.sections:
            if sec.is_alloc and not sec.is_nobits and sec.size > 0:
                segs.append(Segment(sec.addr if use_vaddr else to_phys(sec.addr), info.section_data(sec),
                                    sec.offset, sec.name))
        if segs:
            warnings.append("ELF has no loadable program headers; allocated sections were used instead.")
        else:
            warnings.append("ELF contains no loadable data.")
    segs.sort(key=lambda s: s.start)
    for prev, nxt in zip(segs, segs[1:]):
        if nxt.start < prev.end:
            mode = "virtual (VMA)" if use_vaddr else "load (LMA)"
            raise FirmwareFormatError(
                f"ELF loadable segments overlap at {mode} address 0x{nxt.start:08X}. "
                f"Try the {'LMA' if use_vaddr else 'VMA'} address mode instead.")
    return segs, warnings
