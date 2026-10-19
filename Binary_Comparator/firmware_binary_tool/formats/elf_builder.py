"""Tiny ELF32 little-endian writer used to produce example and test files.

This is *not* a linker; it simply lays out the given sections, emits one
PT_LOAD program header per allocated PROGBITS section and writes a symbol
table, which is enough to exercise the ELF parser and ELF comparison.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .elf_parser import SHF_ALLOC, SHF_EXECINSTR, SHF_WRITE


@dataclass
class SectionSpec:
    name: str
    addr: int
    data: bytes | None = None  # None -> NOBITS (e.g. .bss)
    size: int = 0  # only for NOBITS
    flags: int = SHF_ALLOC
    lma: int | None = None  # load address if different from addr (e.g. .data)


TEXT = SHF_ALLOC | SHF_EXECINSTR
RODATA = SHF_ALLOC
RWDATA = SHF_ALLOC | SHF_WRITE


def build_elf32(sections: list[SectionSpec], symbols: list[tuple[str, int, int, str, str]] = (),
                entry: int = 0, machine: int = 40, comment: str = "GCC: (Example) 12.2.1") -> bytes:
    """symbols: (name, value, size, "FUNC" | "OBJECT" | "NOTYPE", section_name)."""
    all_secs = list(sections) + [SectionSpec(".comment", 0, (comment + "\0").encode(), flags=0x30)]
    shstr = bytearray(b"\0")
    name_off = {}
    for s in all_secs + [SectionSpec(".symtab", 0, b""), SectionSpec(".strtab", 0, b""),
                         SectionSpec(".shstrtab", 0, b"")]:
        name_off[s.name] = len(shstr)
        shstr += s.name.encode() + b"\0"

    loadable = [s for s in all_secs if s.data is not None and s.flags & SHF_ALLOC]
    phoff = 52
    data_off = phoff + 32 * len(loadable)
    blob = bytearray()
    offsets = {}
    for s in all_secs:
        while (data_off + len(blob)) % 4:
            blob += b"\0"
        offsets[s.name] = data_off + len(blob)
        if s.data is not None:
            blob += s.data

    # symbol table
    sec_index = {s.name: i + 1 for i, s in enumerate(all_secs)}
    strtab = bytearray(b"\0")
    symtab = bytearray(b"\0" * 16)
    for name, value, size, typ, secname in symbols:
        off = len(strtab)
        strtab += name.encode() + b"\0"
        st_info = (1 << 4) | {"FUNC": 2, "OBJECT": 1, "NOTYPE": 0}.get(typ, 1)
        symtab += struct.pack("<IIIBBH", off, value, size, st_info, 0, sec_index.get(secname, 0xFFF1))
    extra = [(".symtab", bytes(symtab)), (".strtab", bytes(strtab)), (".shstrtab", bytes(shstr))]
    for name, data in extra:
        while (data_off + len(blob)) % 4:
            blob += b"\0"
        offsets[name] = data_off + len(blob)
        blob += data
    while (data_off + len(blob)) % 4:
        blob += b"\0"
    shoff = data_off + len(blob)

    n_sections = 1 + len(all_secs) + 3
    shstrndx = n_sections - 1
    out = bytearray()
    ident = b"\x7fELF" + bytes([1, 1, 1, 0]) + b"\0" * 8
    out += ident + struct.pack("<HHIIIIIHHHHHH", 2, machine, 1, entry, phoff if loadable else 0, shoff,
                               0x05000200, 52, 32, len(loadable), 40, n_sections, shstrndx)
    for s in loadable:
        flags = 5 if s.flags & SHF_EXECINSTR else (6 if s.flags & SHF_WRITE else 4)
        out += struct.pack("<IIIIIIII", 1, offsets[s.name], s.addr, s.lma if s.lma is not None else s.addr,
                           len(s.data), len(s.data), flags, 4)
    out += blob
    out += b"\0" * 40  # null section
    for s in all_secs:
        if s.data is None:
            out += struct.pack("<IIIIIIIIII", name_off[s.name], 8, s.flags, s.addr, offsets[s.name], s.size, 0, 0, 4, 0)
        else:
            out += struct.pack("<IIIIIIIIII", name_off[s.name], 1, s.flags, s.addr, offsets[s.name],
                               len(s.data), 0, 0, 4 if s.flags & SHF_ALLOC else 1, 0)
    strtab_index = 1 + len(all_secs) + 1
    out += struct.pack("<IIIIIIIIII", name_off[".symtab"], 2, 0, 0, offsets[".symtab"], len(symtab),
                       strtab_index, 1, 4, 16)
    out += struct.pack("<IIIIIIIIII", name_off[".strtab"], 3, 0, 0, offsets[".strtab"], len(strtab), 0, 0, 1, 0)
    out += struct.pack("<IIIIIIIIII", name_off[".shstrtab"], 3, 0, 0, offsets[".shstrtab"], len(shstr), 0, 0, 1, 0)
    return bytes(out)
