"""Symbol tables (functions / variables / source modules) from ELF or GNU ld map files.

Symbols let the tool translate "bytes changed at 0x08012A40" into
"function motor_control_loop in motor.o changed". Two sources are supported:

* the ELF of a build (``.symtab``; LOCAL symbols carry their source file)
* a GNU ld linker map (``-Wl,-Map=app.map``), which also names the object
  file / module of *every* symbol.

Each symbol has a VMA (run-time address) and an LMA (load / flash address).
They differ for initialised RAM data (``.data``), whose initial values are
stored in flash. Firmware images are compared at LMA unless an ELF is loaded
in VMA mode.
"""

from __future__ import annotations

import os
import re
from bisect import bisect_right
from dataclasses import dataclass

from ..errors import FirmwareFormatError
from .intervals import IntervalSet

KIND_FUNC, KIND_OBJECT = "FUNC", "OBJECT"


@dataclass
class SymbolInfo:
    name: str
    address: int  # VMA
    load_address: int  # LMA (where its bytes are in the flash image)
    size: int
    kind: str  # FUNC | OBJECT
    section: str = ""
    module: str = ""  # object / source file, '' if unknown

    def addr(self, use_vma: bool = False) -> int:
        return self.address if use_vma else self.load_address

    @property
    def key(self) -> tuple[str, str]:
        return self.name, self.module


class SymbolTable:
    def __init__(self, symbols: list[SymbolInfo], source: str, arch: str | None = None, endian: str = "little",
                 labels: dict[int, set[str]] | None = None):
        self.source = source  # human readable: "FW_V1.2.3.elf (ELF)"
        self.path: str | None = None  # file the table was loaded from (None for an image's own ELF)
        self.arch = arch  # e.g. "ARM" (from ELF); None = unknown (map files)
        self.endian = endian
        # zero-size labels by run-time address: linker symbols such as _end, __init_array_start, _gp
        self.labels: dict[int, frozenset[str]] = {a: frozenset(n) for a, n in (labels or {}).items()}
        self.symbols = sorted((s for s in symbols if s.size > 0), key=lambda s: (s.load_address, -s.size))
        self._index: dict[bool, tuple[list[int], list[SymbolInfo]]] = {}

    def neighbours(self, vma: int) -> tuple[SymbolInfo | None, SymbolInfo | None]:
        """(last symbol starting at or before *vma*, first symbol starting after it), by run-time address."""
        starts, syms = self._sorted(True)
        i = bisect_right(starts, vma)
        return (syms[i - 1] if i > 0 else None), (syms[i] if i < len(syms) else None)

    def names_starting_at(self, vma: int) -> frozenset[str]:
        """All symbol names (aliases) whose run-time address is *vma*."""
        if not hasattr(self, "_starts_by_vma"):
            idx: dict[int, set[str]] = {}
            for s in self.symbols:
                idx.setdefault(s.address, set()).add(s.name)
            self._starts_by_vma = {a: frozenset(n) for a, n in idx.items()}
        return self._starts_by_vma.get(vma, frozenset())

    def labels_at(self, vma: int) -> frozenset[str]:
        return self.labels.get(vma, frozenset())

    def label_value(self, name: str) -> int | None:
        for addr, names in self.labels.items():
            if name in names:
                return addr
        return None

    def __len__(self) -> int:
        return len(self.symbols)

    def _sorted(self, use_vma: bool):
        if use_vma not in self._index:
            syms = sorted(self.symbols, key=lambda s: (s.addr(use_vma), -s.size))
            self._index[use_vma] = ([s.addr(use_vma) for s in syms], syms)
        return self._index[use_vma]

    def symbol_at(self, addr: int, use_vma: bool = False) -> SymbolInfo | None:
        """Smallest symbol containing *addr* (symbols may nest / alias)."""
        starts, syms = self._sorted(use_vma)
        i = bisect_right(starts, addr) - 1
        best = None
        # look back a few entries: an enclosing symbol may start earlier than a nested one
        for j in range(i, max(-1, i - 16), -1):
            s = syms[j]
            if s.addr(use_vma) <= addr < s.addr(use_vma) + s.size and (best is None or s.size < best.size):
                best = s
        return best

    def ranges(self, use_vma: bool = False) -> IntervalSet:
        return IntervalSet((s.addr(use_vma), s.addr(use_vma) + s.size) for s in self.symbols)


# ---------------------------------------------------------------------- ELF
def from_elf(info, source: str = "ELF") -> SymbolTable:
    from ..formats.elf_parser import physical_translator
    thumb = info.machine == "ARM"
    to_phys = physical_translator(info)
    sections = {}
    for sec in info.sections:
        sections.setdefault(sec.name, sec)

    def lma_delta(secname: str) -> int:
        sec = sections.get(secname)
        if sec is None or not sec.is_alloc:
            return 0
        for ph in info.program_headers:
            if ph.type == 1 and ph.vaddr <= sec.addr < ph.vaddr + max(ph.memsz, 1):
                return ph.paddr - ph.vaddr
        return 0

    deltas: dict[str, int] = {}
    out = []
    labels: dict[int, set[str]] = {}
    for s in info.symbols:
        if s.size == 0 and s.section != "UND" and s.name and not s.name.startswith("$"):
            labels.setdefault(s.value, set()).add(s.name)
            if thumb and s.type == KIND_FUNC:
                labels.setdefault(s.value & ~1, set()).add(s.name)
            continue
        if s.type not in (KIND_FUNC, KIND_OBJECT) or s.size == 0 or s.section in ("UND", "ABS", "COMMON"):
            continue
        addr = s.value & ~1 if (thumb and s.type == KIND_FUNC) else s.value  # clear Thumb bit
        if s.section not in deltas:
            deltas[s.section] = lma_delta(s.section)
        out.append(SymbolInfo(s.name, addr, to_phys(addr + deltas[s.section]), s.size, s.type, s.section,
                              os.path.basename(s.file) if s.file else ""))
    return SymbolTable(out, source, info.machine, info.endian, labels)


# ------------------------------------------------------------------ GNU map
_HEX = r"0x([0-9A-Fa-f]+)"
_OUT_SEC = re.compile(rf"^(\S+)\s+{_HEX}\s+{_HEX}(?:\s+load address {_HEX})?\s*$")
_OUT_CONT = re.compile(rf"^\s+{_HEX}\s+{_HEX}(?:\s+load address {_HEX})?\s*$")
_IN_SEC = re.compile(rf"^ (\S+)\s+{_HEX}\s+{_HEX}\s+(\S.*)$")
_IN_CONT = re.compile(rf"^\s+{_HEX}\s+{_HEX}\s+(\S.*)$")
_SYM = re.compile(rf"^\s{{8,}}{_HEX}\s+([A-Za-z_.$][\w.$@]*)\s*$")
_NAME_ONLY = re.compile(r"^( ?)(\S+)\s*$")
_ASSIGN = re.compile(rf"^\s+{_HEX}\s+(?:PROVIDE\s*\(\s*)?([A-Za-z_.$][\w.$]*)\s*=")


def _module_name(obj: str) -> str:
    obj = obj.strip()
    m = re.search(r"\(([^()]+)\)\s*$", obj)  # libfoo.a(bar.o) -> bar.o
    if m:
        obj = m.group(1)
    return os.path.basename(obj.replace("\\", "/"))


def parse_gnu_map(path: str) -> SymbolTable:
    """Parse the 'Linker script and memory map' part of a GNU ld map file."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError as exc:
        raise FirmwareFormatError(f"Cannot read map file: {exc}") from None
    start = next((i for i, ln in enumerate(lines) if ln.startswith("Linker script and memory map")), None)
    if start is None:
        raise FirmwareFormatError(f"{os.path.basename(path)}: not a GNU ld map file "
                                  "(missing 'Linker script and memory map' section)")

    out_delta = 0  # LMA - VMA of the current output section
    out_name = ""
    pending_out: str | None = None
    pending_in: str | None = None
    current_in = None  # [section, vma, size, module]
    in_sections: list[list] = []
    raw_syms: list[tuple[int, str, int | None, int]] = []  # (vma, name, size or None, input section index)
    labels: dict[int, set[str]] = {}
    out_vmas: list[tuple[str, int]] = []

    def open_input(sec, vma, size, obj):
        nonlocal current_in
        current_in = [sec, vma, size, _module_name(obj), out_delta, out_name]
        in_sections.append(current_in)

    for line in lines[start + 1:]:
        if not line.strip():
            continue
        if pending_out is not None:
            m = _OUT_CONT.match(line)
            if m:
                out_name = pending_out
                vma = int(m.group(1), 16)
                out_delta = int(m.group(3), 16) - vma if m.group(3) else 0
                pending_out = None
                current_in = None
                continue
            pending_out = None
        if pending_in is not None:
            m = _IN_CONT.match(line)
            pending_name, pending_in = pending_in, None
            if m:
                open_input(pending_name, int(m.group(1), 16), int(m.group(2), 16), m.group(3))
                continue
        if not line.startswith(" "):
            m = _OUT_SEC.match(line)
            if m:
                out_name = m.group(1)
                vma = int(m.group(2), 16)
                out_vmas.append((out_name, vma))
                out_delta = int(m.group(4), 16) - vma if m.group(4) else 0
                current_in = None
                continue
            m = _NAME_ONLY.match(line)
            if m and m.group(2).startswith("."):
                pending_out = m.group(2)
            continue
        m = _ASSIGN.match(line)
        if m:  # linker-defined label, e.g. "_end = ." / "PROVIDE (end = .)"
            labels.setdefault(int(m.group(1), 16), set()).add(m.group(2))
            continue
        if line.startswith(" *fill*") or line.lstrip().startswith(("*(", "LOAD ", "OUTPUT(", "PROVIDE")):
            continue
        m = _IN_SEC.match(line)
        if m and not line.startswith("  "):
            open_input(m.group(1), int(m.group(2), 16), int(m.group(3), 16), m.group(4))
            continue
        if line.startswith(" ") and not line.startswith("  "):
            m = _NAME_ONLY.match(line)
            if m:
                pending_in = m.group(2)
                continue
        m = _SYM.match(line)
        if m and current_in is not None and "=" not in line:
            raw_syms.append((int(m.group(1), 16), m.group(2), None, len(in_sections) - 1))
            continue
        m = _IN_CONT.match(line)  # COMMON symbols: "   0xADDR   0xSIZE name"
        if m and current_in is not None and current_in[0] == "COMMON":
            raw_syms.append((int(m.group(1), 16), m.group(3).strip(), int(m.group(2), 16), len(in_sections) - 1))

    # PIC32 (MIPS32) maps use KSEG0/KSEG1 addresses; HEX files use physical addresses
    pic32 = any(n == ".reset" or n.startswith(".vector_") or 0xBFC00000 <= v < 0xBFC80000 for n, v in out_vmas)
    to_phys = (lambda a: a & 0x1FFFFFFF if 0x80000000 <= a < 0xC0000000 else a) if pic32 else (lambda a: a)
    symbols = []
    by_section: dict[int, list] = {}
    for rs in raw_syms:
        by_section.setdefault(rs[3], []).append(rs)
    for idx, syms in by_section.items():
        sec, sec_vma, sec_size, module, delta, oname = in_sections[idx]
        sec_end = sec_vma + sec_size
        syms.sort()
        for k, (vma, name, size, _) in enumerate(syms):
            if size is None:
                nxt = next((s[0] for s in syms[k + 1:] if s[0] > vma), sec_end)
                size = max(0, min(nxt, sec_end) - vma)
            # function sections are .text / .text.<name> (also when placed in a custom output section)
            kind = KIND_FUNC if sec.startswith((".text", ".itcm", ".ramfunc")) else KIND_OBJECT
            if size == 0:
                labels.setdefault(vma, set()).add(name)
                continue
            symbols.append(SymbolInfo(name, vma, to_phys(vma + delta), size, kind, oname or sec, module))
    if not symbols:
        raise FirmwareFormatError(f"{os.path.basename(path)}: no symbols found in the memory map section")
    return SymbolTable(symbols, f"{os.path.basename(path)} (map)", "MIPS" if pic32 else None, "little", labels)


def load_symbol_table(path: str) -> SymbolTable:
    """Load symbols from an ELF file or a GNU ld .map file."""
    if not os.path.isfile(path):
        raise FirmwareFormatError(f"Symbol file not found: {path}")
    with open(path, "rb") as f:
        magic = f.read(4)
    if magic == b"\x7fELF":
        from ..formats.elf_parser import parse_elf
        table = from_elf(parse_elf(path), f"{os.path.basename(path)} (ELF)")
    else:
        table = parse_gnu_map(path)
    table.path = path
    return table
