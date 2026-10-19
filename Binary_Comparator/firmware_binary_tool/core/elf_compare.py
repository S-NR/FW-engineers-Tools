"""Section- and symbol-level comparison of two ELF files."""

from __future__ import annotations

from dataclasses import dataclass, field

from .binary_comparator import count_diff_bytes

SEC_SAME, SEC_CHANGED, SEC_ADDED, SEC_REMOVED = "SAME", "CHANGED", "ADDED", "REMOVED"


@dataclass
class SectionDiff:
    name: str
    type_name: str
    flags: str
    addr_a: int | None
    addr_b: int | None
    size_a: int | None
    size_b: int | None
    diff_bytes: int  # differing content bytes (incl. size difference)
    status: str
    notes: list[str] = field(default_factory=list)


@dataclass
class SymbolDiff:
    name: str
    status: str  # ADDED / REMOVED / MOVED / RESIZED / MOVED+RESIZED
    addr_a: int | None
    addr_b: int | None
    size_a: int | None
    size_b: int | None
    type: str


@dataclass
class ElfComparison:
    sections: list[SectionDiff]
    symbols: list[SymbolDiff]
    symbol_count_a: int
    symbol_count_b: int
    build_info_a: dict[str, str]
    build_info_b: dict[str, str]

    @property
    def changed_sections(self) -> list[SectionDiff]:
        return [s for s in self.sections if s.status != SEC_SAME]


def _keyed_sections(info) -> dict[tuple[str, int], object]:
    out, seen = {}, {}
    for s in info.sections:
        if s.type == 0:  # null section
            continue
        n = seen.get(s.name, 0)
        seen[s.name] = n + 1
        out[(s.name, n)] = s
    return out


def compare_elf_sections(info_a, info_b) -> list[SectionDiff]:
    sa, sb = _keyed_sections(info_a), _keyed_sections(info_b)
    keys = list(sa) + [k for k in sb if k not in sa]
    result = []
    for key in keys:
        a, b = sa.get(key), sb.get(key)
        ref = a or b
        name = key[0] if key[1] == 0 else f"{key[0]} #{key[1] + 1}"
        if a is None or b is None:
            size = ref.size
            result.append(SectionDiff(name, ref.type_name, ref.flag_str,
                                      a.addr if a else None, b.addr if b else None,
                                      a.size if a else None, b.size if b else None,
                                      0 if ref.is_nobits else size, SEC_ADDED if a is None else SEC_REMOVED))
            continue
        notes = []
        if a.addr != b.addr:
            notes.append(f"address moved 0x{a.addr:X} -> 0x{b.addr:X}")
        if a.size != b.size:
            notes.append(f"size {a.size:,} -> {b.size:,} ({b.size - a.size:+,} bytes)")
        if a.type != b.type:
            notes.append(f"type {a.type_name} -> {b.type_name}")
        if a.is_nobits or b.is_nobits:
            diff = 0 if a.size == b.size else abs(a.size - b.size)
            if a.is_nobits and b.is_nobits:
                notes.append("NOBITS section: no file content, only size/address compared")
        else:
            diff = count_diff_bytes(info_a.section_data(a), info_b.section_data(b))
        status = SEC_SAME if not notes and diff == 0 else SEC_CHANGED
        if a.is_nobits and b.is_nobits and a.size == b.size and a.addr == b.addr:
            status = SEC_SAME
        result.append(SectionDiff(name, a.type_name, a.flag_str, a.addr, b.addr, a.size, b.size, diff, status, notes))
    return result


def compare_symbols(info_a, info_b) -> list[SymbolDiff]:
    def table(info):
        d = {}
        for s in info.symbols:
            if s.section == "UND":
                continue
            d.setdefault(s.name, s)  # first definition wins (static duplicates are rare in firmware)
        return d

    ta, tb = table(info_a), table(info_b)
    out = []
    for name in sorted(set(ta) | set(tb)):
        a, b = ta.get(name), tb.get(name)
        if a is None:
            out.append(SymbolDiff(name, "ADDED", None, b.value, None, b.size, b.type))
        elif b is None:
            out.append(SymbolDiff(name, "REMOVED", a.value, None, a.size, None, a.type))
        else:
            parts = []
            if a.value != b.value:
                parts.append("MOVED")
            if a.size != b.size:
                parts.append("RESIZED")
            if parts:
                out.append(SymbolDiff(name, "+".join(parts), a.value, b.value, a.size, b.size, a.type))
    return out


def compare_elf(info_a, info_b) -> ElfComparison:
    return ElfComparison(
        sections=compare_elf_sections(info_a, info_b),
        symbols=compare_symbols(info_a, info_b),
        symbol_count_a=len(info_a.symbols),
        symbol_count_b=len(info_b.symbols),
        build_info_a=dict(info_a.build_info),
        build_info_b=dict(info_b.build_info),
    )
