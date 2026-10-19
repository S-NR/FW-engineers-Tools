"""Feature-level comparison: which functions, variables and modules changed.

A binary image has no notion of "features". The closest reliable proxy is the
symbol information of the builds: every function / variable is compared by
*content*, at each build's own address.

Removing or adding code shifts everything linked after it. That changes the
bytes of many functions that were not edited:

* call / branch instructions (``BL``, ``B.W``) encode PC-relative offsets, so a
  caller changes when its callee moves by a different amount;
* literal pools, vector tables and pointer tables hold absolute addresses.

To separate real edits from these side effects, every differing branch or
pointer is decoded in both builds. If both resolve to the *same symbol + offset*
(or to identical content, e.g. the same string literal), the difference is an
address-reference change only. A function whose differences are all of that
kind is reported as RELOCATED, not MODIFIED.

* Symbols for both builds (ELF/map for A and B): symbols are matched by name
  (and module) and each side's bytes are read at that side's address.
* Symbols for only one build: the same layout is assumed for both images
  (address-based) and this is stated in the report notes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .binary_comparator import count_diff_bytes
from .symbols import SymbolInfo, SymbolTable

MODIFIED, ADDED, REMOVED = "MODIFIED", "ADDED", "REMOVED"
RELOCATED, MOVED, UNCHANGED, NOT_IN_IMAGE = "RELOCATED", "MOVED", "UNCHANGED", "NOT IN IMAGE"
LIKELY_RELOCATED = "LIKELY RELOCATED"
REAL_CHANGES = (MODIFIED, ADDED, REMOVED)  # the source / build content really differs
SIDE_EFFECTS = (RELOCATED, MOVED)  # only addresses differ because other code changed size
UNCERTAIN = (LIKELY_RELOCATED,)  # only address references differ, some to unnamed data/code
CHANGED_STATUSES = REAL_CHANGES + UNCERTAIN + SIDE_EFFECTS

STATUS_HELP = {
    MODIFIED: "content changed",
    ADDED: "only in the new build",
    REMOVED: "only in the old build",
    RELOCATED: "only call targets / pointers changed because referenced code or data moved",
    MOVED: "identical content at a different address",
    LIKELY_RELOCATED: "only address references differ, but some point to unnamed data/code and cannot be verified",
    UNCHANGED: "identical",
    NOT_IN_IMAGE: "RAM-only symbol, no bytes in the image",
}


@dataclass
class SymbolChange:
    name: str
    kind: str
    module: str
    section: str
    status: str
    addr_a: int | None
    addr_b: int | None
    size_a: int | None
    size_b: int | None
    changed_bytes: int = 0  # bytes that really changed (address-reference changes excluded)
    reloc_bytes: int = 0  # differing bytes explained as address references to moved code/data
    unverified_bytes: int = 0  # decoded address references whose targets have no symbol (cannot be verified)
    expected_bytes: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def address(self) -> int | None:
        return self.addr_b if self.addr_b is not None else self.addr_a

    @property
    def size_delta(self) -> int:
        return (self.size_b or 0) - (self.size_a or 0)

    @property
    def expected_only(self) -> bool:
        return self.status == MODIFIED and self.changed_bytes > 0 and self.expected_bytes >= self.changed_bytes

    @property
    def is_real_change(self) -> bool:
        return self.status in REAL_CHANGES


@dataclass
class ModuleChange:
    module: str
    modified: int = 0
    added: int = 0
    removed: int = 0
    moved: int = 0  # moved or relocated only (side effects)
    changed_bytes: int = 0
    expected_only_symbols: int = 0  # modified symbols whose changes are all in expected areas
    symbols: list[str] = field(default_factory=list)

    @property
    def total_changed(self) -> int:
        return self.modified + self.added + self.removed

    @property
    def expected_only(self) -> bool:
        """True when every change in this module lies inside configured expected areas."""
        return self.total_changed > 0 and self.expected_only_symbols == self.total_changed

    @property
    def status(self) -> str:
        if self.total_changed == 0:
            return RELOCATED
        return "EXPECTED" if self.expected_only else "CHANGED"


@dataclass
class FeatureReport:
    source_a: str
    source_b: str
    symbols: list[SymbolChange]
    modules: list[ModuleChange]  # modules with real changes
    attributed_bytes: int  # changed image bytes that fall inside a known symbol
    unattributed_bytes: int  # changed image bytes outside every known symbol
    notes: list[str] = field(default_factory=list)
    reloc_modules: int = 0  # modules whose symbols only moved / were relocated

    @property
    def changed(self) -> list[SymbolChange]:
        """Real changes only (modified / added / removed)."""
        return [s for s in self.symbols if s.status in REAL_CHANGES]

    @property
    def side_effects(self) -> list[SymbolChange]:
        return [s for s in self.symbols if s.status in SIDE_EFFECTS]

    @property
    def uncertain(self) -> list[SymbolChange]:
        return [s for s in self.symbols if s.status in UNCERTAIN]

    def count(self, status: str) -> int:
        return sum(1 for s in self.symbols if s.status == status)

    def headline(self) -> str:
        likely = f", {self.count(LIKELY_RELOCATED)} likely relocated" if self.count(LIKELY_RELOCATED) else ""
        return (f"{self.count(MODIFIED)} modified, {self.count(ADDED)} added, {self.count(REMOVED)} removed"
                f"{likely} ({self.count(RELOCATED)} relocated, {self.count(MOVED)} moved only)")


def uses_vma(image) -> bool:
    return image is not None and image.format == "elf" and image.info.get("Address mode", "").startswith("VMA")


def _content(image, addr: int, size: int) -> bytes | None:
    """Bytes of [addr, addr+size) if the image holds all of them, else None."""
    if image.coverage.overlap(addr, addr + size) != size:
        return None
    return b"".join(image.iter_range(addr, addr + size))


# ------------------------------------------------------------ relocation analysis
class _Resolver:
    """Turns a run-time address (branch target / pointer value) into build-independent keys."""

    def __init__(self, table: SymbolTable, image, image_uses_vma: bool, to_image=None):
        self.table, self.image, self.image_vma = table, image, image_uses_vma
        self.to_image = to_image or (lambda a: a)  # run-time address -> image address (e.g. MIPS KSEG -> phys)
        self._sym: dict[int, tuple | None] = {}
        self._data: dict[int, tuple | None] = {}
        self.gp = table.label_value("_gp")  # MIPS global pointer

    def labels(self, value: int) -> frozenset[str]:
        """Names of zero-size linker labels at the address (e.g. _end, __init_array_start)."""
        return self.table.labels_at(value) | self.table.labels_at(value & ~1)

    def sym_key(self, value: int):
        if value not in self._sym:
            result = None
            for v, tag in ((value, 0), (value & ~1, value & 1)):  # bit 0 = Thumb function pointer
                s = self.table.symbol_at(v, use_vma=True)
                if s is not None:  # all aliases at the symbol start, so differently named aliases still match
                    result = (self.table.names_starting_at(s.address), v - s.address, tag)
                    break
            self._sym[value] = result
        return self._sym[value]

    def anchor_keys(self, value: int) -> set[tuple]:
        """Unnamed target (e.g. a static function without symbol): position relative to its named neighbours."""
        prev, nxt = self.table.neighbours(value)
        keys = set()
        if prev is not None and value - prev.address < 0x10000:
            keys.update(("prev", n, value - prev.address) for n in self.table.names_starting_at(prev.address))
        if nxt is not None and nxt.address - value < 0x10000:
            keys.update(("next", n, nxt.address - value) for n in self.table.names_starting_at(nxt.address))
        return keys

    def data_key(self, value: int):
        """Unnamed target (e.g. a string literal): identify it by the bytes it points to."""
        if value not in self._data:
            s = self.table.symbol_at(value, use_vma=True)
            addr = s.addr(self.image_vma) + (value - s.address) if s is not None else self.to_image(value)
            data = bytearray()
            for b in self.image.read(addr, 64):
                if b is None:
                    break
                data.append(b)
                if b == 0 and len(data) >= 4:
                    break
            self._data[value] = ("data", bytes(data)) if len(data) >= 4 else None
        return self._data[value]


def _same_target(ra: "_Resolver", rb: "_Resolver", va: int | None, vb: int | None) -> bool:
    """True when two addresses (one per build) refer to the same thing."""
    if va is None or vb is None:
        return False
    la, lb = ra.labels(va), rb.labels(vb)
    if la and lb and la & lb:
        return True
    ka, kb = ra.sym_key(va), rb.sym_key(vb)
    if ka is not None or kb is not None:
        return ka is not None and kb is not None and ka[1:] == kb[1:] and bool(ka[0] & kb[0])
    da = ra.data_key(va)
    if da is not None and da == rb.data_key(vb):
        return True
    return bool(ra.anchor_keys(va) & rb.anchor_keys(vb))


# ------------------------------------------------------------------ MIPS32 decoding
_MIPS_LO_OPS = {0x09, 0x0D, 0x20, 0x21, 0x23, 0x24, 0x25, 0x28, 0x29, 0x2B, 0x31, 0x35, 0x39, 0x3D}


def _mips_hilo_pairs(code: bytes, endian: str) -> dict[tuple[int, int], int]:
    """Nearby %hi/%lo pairs: (LUI offset, using offset) -> address, last LUI to the base register within 64 bytes."""
    out: dict[tuple[int, int], int] = {}
    last_lui: dict[int, tuple[int, int]] = {}
    for off in range(0, len(code) - 3, 4):
        w = int.from_bytes(code[off:off + 4], endian)
        op = w >> 26
        rs, rt, imm = (w >> 21) & 31, (w >> 16) & 31, w & 0xFFFF
        if op == 0x0F:
            last_lui[rt] = (off, imm << 16)
        elif op in _MIPS_LO_OPS and rs in last_lui and off - last_lui[rs][0] <= 64:
            lui_off, upper = last_lui[rs]
            low = imm if op == 0x0D else (imm - 0x10000 if imm & 0x8000 else imm)
            out[(lui_off, off)] = (upper + low) & 0xFFFFFFFF
    return out


_MIPS_STORES = {0x28, 0x29, 0x2A, 0x2B, 0x2E, 0x31, 0x35, 0x39, 0x3D}  # do not write a GPR
_MIPS_CALLER_SAVED = set(range(1, 16)) | {24, 25, 31}


def _mips_address_refs(code: bytes, endian: str, gp: int | None = None) -> dict[tuple[int, int], int]:
    """(defining LUI offset or -1 for $gp, using instruction offset) -> absolute address referenced.

    Tracks registers that hold LUI-built addresses through the function (linear scan): a register keeps its
    value until it is overwritten, and caller-saved registers are cleared after calls. ADDIU on a known
    register propagates the address, so ``lui; addiu; lw 8(reg)`` chains are resolved too.
    """
    out: dict[tuple[int, int], int] = {}
    known: dict[int, tuple[int, int]] = {}  # reg -> (address value, origin LUI offset)
    if gp is not None:
        known[28] = (gp, -1)
    clobber_after_slot = 0  # a call clobbers registers only after its branch delay slot has executed
    for off in range(0, len(code) - 3, 4):
        if clobber_after_slot:
            clobber_after_slot -= 1
            if clobber_after_slot == 0:
                for r in _MIPS_CALLER_SAVED:
                    known.pop(r, None)
        w = int.from_bytes(code[off:off + 4], endian)
        op = w >> 26
        rs, rt, rd, imm = (w >> 21) & 31, (w >> 16) & 31, (w >> 11) & 31, w & 0xFFFF
        simm = imm - 0x10000 if imm & 0x8000 else imm
        if op == 0x0F:  # LUI
            known[rt] = (imm << 16, off)
            continue
        if op in _MIPS_LO_OPS and rs in known:
            base, origin = known[rs]
            addr = ((base | imm) if op == 0x0D else (base + simm)) & 0xFFFFFFFF
            out[(origin, off)] = addr
            if op in (0x09, 0x0D):  # ADDIU / ORI: rt now holds the full address
                if rt != 0:
                    known[rt] = (addr, origin)
                continue
        # register writes invalidate tracked values
        if op == 0:
            funct = w & 0x3F
            if funct == 0x09:  # JALR: caller-saved registers die after the delay slot
                clobber_after_slot = 2
            if funct not in (0x08, 0x0C, 0x0D, 0x11, 0x13, 0x18, 0x19, 0x1A, 0x1B):
                known.pop(rd, None)
        elif op == 0x03:  # JAL
            clobber_after_slot = 2
        elif op == 0x1C:  # SPECIAL2 (MUL...)
            known.pop(rd, None)
        elif op == 0x1F or 0x08 <= op <= 0x0E or 0x20 <= op <= 0x27:  # SPECIAL3, ALU-immediate, loads
            if rt != 28 or op != 0x23:  # keep $gp unless it is reloaded
                known.pop(rt, None)
    return out


def _mips_jump_target(code: bytes, off: int, pc_base: int, endian: str) -> int | None:
    if off < 0 or off + 4 > len(code):
        return None
    w = int.from_bytes(code[off:off + 4], endian)
    if w >> 26 not in (2, 3):  # J / JAL
        return None
    return ((pc_base + off + 4) & 0xF0000000) | ((w & 0x3FFFFFF) << 2)


def _mips_gp_address(code: bytes, off: int, gp: int | None, endian: str) -> int | None:
    """Address accessed by a $gp-relative load/store/addiu (small data), or None."""
    if gp is None or off < 0 or off + 4 > len(code):
        return None
    w = int.from_bytes(code[off:off + 4], endian)
    if w >> 26 not in _MIPS_LO_OPS or ((w >> 21) & 31) != 28:
        return None
    imm = w & 0xFFFF
    return gp + (imm - 0x10000 if imm & 0x8000 else imm)


def _thumb_branch_target(code: bytes, off: int, pc_base: int) -> int | None:
    """Target of a 32-bit Thumb-2 BL / BLX / B.W (T4) at *off*, or None."""
    if off < 0 or off + 4 > len(code):
        return None
    hw1 = code[off] | (code[off + 1] << 8)
    hw2 = code[off + 2] | (code[off + 3] << 8)
    if hw1 >> 11 != 0b11110:
        return None
    is_bl = (hw2 & 0xD000) == 0xD000
    is_blx = (hw2 & 0xD001) == 0xC000
    is_bw = (hw2 & 0xD000) == 0x9000
    if not (is_bl or is_blx or is_bw):
        return None
    s = (hw1 >> 10) & 1
    i1 = 1 ^ (((hw2 >> 13) & 1) ^ s)
    i2 = 1 ^ (((hw2 >> 11) & 1) ^ s)
    imm = (s << 24) | (i1 << 23) | (i2 << 22) | ((hw1 & 0x3FF) << 12) | ((hw2 & 0x7FF) << 1)
    if s:
        imm -= 1 << 25
    pc = pc_base + off + 4
    if is_blx:
        return (pc & ~3) + imm
    return (pc + imm) | 1


def _arm_branch_target(code: bytes, off: int, pc_base: int) -> int | None:
    """Target of a 32-bit ARM-mode B/BL at *off* (little endian), or None."""
    if off < 0 or off + 4 > len(code):
        return None
    word = int.from_bytes(code[off:off + 4], "little")
    if (word >> 25) & 0x7 != 0b101 or (word >> 28) == 0xF:
        return None
    imm = word & 0xFFFFFF
    if imm & 0x800000:
        imm -= 1 << 24
    return pc_base + off + 8 + (imm << 2)


def _explain_differences(ca: bytes, cb: bytes, base_a: int, base_b: int, ra: _Resolver, rb: _Resolver,
                         isa: str, endian: str) -> tuple[int, int, int]:
    """For equal-size contents return (unexplained, address-reference, unverifiable-reference) byte counts.

    *Unverifiable* bytes belong to decoded instructions (calls, %hi/%lo pairs, $gp accesses) whose targets
    have no symbol in either build, so equivalence cannot be proven either way.
    """
    n = len(ca)
    explained = bytearray(n)
    unverified = bytearray(n)
    diffs = [i for i in range(n) if ca[i] != cb[i]]
    arm, mips = isa == "arm", isa == "mips"

    def same(ta, tb) -> bool:
        return _same_target(ra, rb, ta, tb)

    def unknown(ta, tb) -> bool:
        return (ta is not None and tb is not None and ra.sym_key(ta) is None and rb.sym_key(tb) is None
                and not ra.labels(ta) and not rb.labels(tb))

    def mark_unverified(off: int) -> None:
        if 0 <= off <= n - 4:
            unverified[off:off + 4] = b"\x01" * 4

    if mips:
        # two complementary views of address-building code: nearby LUI/LO pairs, and tracked base registers
        by_off: dict[int, list[tuple[int, int, int, int]]] = {}
        for pa_, pb_ in ((_mips_hilo_pairs(ca, endian), _mips_hilo_pairs(cb, endian)),
                         (_mips_address_refs(ca, endian, ra.gp), _mips_address_refs(cb, endian, rb.gp))):
            for key, addr_a in pa_.items():
                if key in pb_:
                    entry = (key[0], key[1], addr_a, pb_[key])
                    if key[0] >= 0:
                        by_off.setdefault(key[0], []).append(entry)
                    by_off.setdefault(key[1], []).append(entry)

    for i in diffs:
        if explained[i]:
            continue
        done = False
        unverifiable_at = None  # instruction offset whose decoded targets have no symbol on either side
        if mips:
            w = i - (i % 4)
            ja, jb = _mips_jump_target(ca, w, base_a, endian), _mips_jump_target(cb, w, base_b, endian)
            ga, gb = _mips_gp_address(ca, w, ra.gp, endian), _mips_gp_address(cb, w, rb.gp, endian)
            if same(ja, jb) or same(ga, gb):
                explained[w:w + 4] = b"\x01" * 4
                continue
            candidates = by_off.get(w, ())
            for lui_off, lo_off, addr_a, addr_b in candidates:
                if same(addr_a, addr_b):
                    if lui_off >= 0:
                        explained[lui_off:lui_off + 4] = b"\x01" * 4
                    explained[lo_off:lo_off + 4] = b"\x01" * 4
                    done = True
            if done:
                continue
            if unknown(ja, jb) or unknown(ga, gb) or any(unknown(x, y) for _, _, x, y in candidates):
                unverifiable_at = w
        if arm:
            h = i - ((base_a + i) % 2)
            for st in (h, h - 2):  # Thumb-2 32-bit branch covering byte i
                ta, tb = _thumb_branch_target(ca, st, base_a), _thumb_branch_target(cb, st, base_b)
                if same(ta, tb):
                    explained[st:st + 4] = b"\x01" * 4
                    done = True
                    break
                if unknown(ta, tb) and unverifiable_at is None:
                    unverifiable_at = st
        w = i - ((base_a + i) % 4)
        if not done and arm and same(_arm_branch_target(ca, w, base_a), _arm_branch_target(cb, w, base_b)):
            explained[w:w + 4] = b"\x01" * 4
            done = True
        if not done and 0 <= w and w + 4 <= n:  # literal pool word / pointer
            va = int.from_bytes(ca[w:w + 4], endian)
            vb = int.from_bytes(cb[w:w + 4], endian)
            if same(va, vb):
                explained[w:w + 4] = b"\x01" * 4
                done = True
        if not done and unverifiable_at is not None:  # only after every verifiable explanation failed
            mark_unverified(unverifiable_at)
    reloc = sum(1 for i in diffs if explained[i])
    unver = sum(1 for i in diffs if unverified[i] and not explained[i])
    return len(diffs) - reloc - unver, reloc, unver


def _alias_groups(table: SymbolTable) -> tuple[list[SymbolInfo], dict[int, list[SymbolInfo]]]:
    """Collapse aliases (same address and size) into one representative symbol.

    Returns (representatives, id(representative) -> all aliases). A representative carries the first
    non-empty module of its aliases.
    """
    groups: dict[tuple[int, int], list[SymbolInfo]] = {}
    for s in table.symbols:
        groups.setdefault((s.address, s.size), []).append(s)
    reps, aliases = [], {}
    for members in groups.values():
        rep = members[0]
        if not rep.module:
            mod = next((m.module for m in members if m.module), "")
            if mod:
                rep = SymbolInfo(rep.name, rep.address, rep.load_address, rep.size, rep.kind, rep.section, mod)
        reps.append(rep)
        aliases[id(rep)] = members
    return reps, aliases


def _match(ta: SymbolTable, tb: SymbolTable):
    """Pair symbols of both builds. Returns (pairs, aliases_by_id)."""
    reps_a, al_a = _alias_groups(ta)
    reps_b, al_b = _alias_groups(tb)
    aliases = {**al_a, **al_b}

    def index(reps, al):
        by_key, by_name = {}, {}
        for rep in reps:
            for s in al[id(rep)]:
                by_key.setdefault((s.name, s.module or rep.module), []).append(rep)
                by_name.setdefault(s.name, []).append(rep)
        return by_key, by_name

    kb, nb = index(reps_b, al_b)
    pairs, used_b = [], set()
    for rep in reps_a:
        sb = None
        for s in al_a[id(rep)]:
            cands = [c for c in kb.get((s.name, s.module or rep.module), []) if id(c) not in used_b]
            if not cands:  # module unknown on one side: match by unique name
                by_name = [c for c in nb.get(s.name, []) if id(c) not in used_b]
                cands = by_name if len(by_name) == 1 else []
            if cands:
                sb = cands[0]
                break
        if sb is not None:
            used_b.add(id(sb))
        pairs.append((rep, sb))
    pairs += [(None, rep) for rep in reps_b if id(rep) not in used_b]
    return pairs, aliases


_GENERATED_SUFFIX = re.compile(r"(\.\d+)+$")


def _pair_renamed(pairs, content_a, content_b):
    """Re-pair unmatched symbols whose compiler-generated names differ between builds.

    GCC names local statics and constant objects with build-specific numbers (``C.55.31329``,
    ``counter.1234``, ``__func__.4567``). Unmatched symbols with the same base name, module and kind are
    paired: first those with identical content, then a remaining unique 1:1 candidate.
    """
    matched = [p for p in pairs if p[0] is not None and p[1] is not None]
    only_a = [p[0] for p in pairs if p[1] is None]
    only_b = [p[1] for p in pairs if p[0] is None]

    def group(syms):
        g: dict[tuple, list] = {}
        for s_ in syms:
            base = _GENERATED_SUFFIX.sub("", s_.name)
            if base != s_.name:
                g.setdefault((base, s_.module, s_.kind), []).append(s_)
        return g

    ga, gb = group(only_a), group(only_b)
    used = set()
    for key, cands_a in ga.items():
        cands_b = gb.get(key, [])
        if not cands_b:
            continue
        by_content: dict[bytes, list] = {}
        for sb in cands_b:
            by_content.setdefault(content_b(sb), []).append(sb)
        rest_a = []
        for sa in cands_a:
            c = content_a(sa)
            hits = [x for x in by_content.get(c, []) if id(x) not in used] if c is not None else []
            if hits:
                used.update((id(sa), id(hits[0])))
                matched.append((sa, hits[0]))
            else:
                rest_a.append(sa)
        rest_b = [x for x in cands_b if id(x) not in used]
        if len(rest_a) == 1 and len(rest_b) == 1:
            used.update((id(rest_a[0]), id(rest_b[0])))
            matched.append((rest_a[0], rest_b[0]))
    matched += [(sa, None) for sa in only_a if id(sa) not in used]
    matched += [(None, sb) for sb in only_b if id(sb) not in used]
    return matched


def compare_features(session, table_a: SymbolTable | None, table_b: SymbolTable | None) -> FeatureReport | None:
    if table_a is None and table_b is None:
        return None
    img_a, img_b = session.image_a, session.image_b
    an, diff = session.analysis, session.diff
    notes = []
    if table_a is None or table_b is None:
        only = table_a or table_b
        notes.append(f"Symbols are available for only one build ({only.source}); the same memory layout is "
                     "assumed for both images. Load symbols for both builds for exact results.")
        table_a = table_b = only
    vma_a, vma_b = uses_vma(img_a), uses_vma(img_b)
    arch = table_b.arch or table_a.arch
    # map files carry no architecture unless recognised (PIC32): assume ARM/Thumb (Cortex-M)
    isa = "mips" if arch == "MIPS" else ("arm" if arch in (None, "ARM") else None)
    endian = table_b.endian
    from ..formats.elf_parser import mips_kseg_to_phys
    to_img = mips_kseg_to_phys if arch == "MIPS" else None
    ra = _Resolver(table_a, img_a, vma_a, None if vma_a else to_img)
    rb = _Resolver(table_b, img_b, vma_b, None if vma_b else to_img)

    def content(sym, img, vma):
        return _content(img, sym.addr(vma), sym.size)

    matched, aliases = _match(table_a, table_b)
    pairs = _pair_renamed(matched, lambda s_: content(s_, img_a, vma_a), lambda s_: content(s_, img_b, vma_b))

    changes: list[SymbolChange] = []
    for sa, sb in pairs:
        ref = sb or sa
        ch = SymbolChange(ref.name, ref.kind, ref.module or (sa.module if sa else ""), ref.section,
                          UNCHANGED, sa.addr(vma_a) if sa else None, sb.addr(vma_b) if sb else None,
                          sa.size if sa else None, sb.size if sb else None)
        names_a = {x.name for x in aliases.get(id(sa), [])} if sa else set()
        names_b = {x.name for x in aliases.get(id(sb), [])} if sb else set()
        if sa is not None and sb is not None and not names_a & names_b:
            ch.notes.append(f"compiler-generated name {sa.name} -> {sb.name}")
        other = sorted((names_a | names_b) - {ref.name})
        if other:
            ch.notes.append("aliases: " + ", ".join(other[:6]) + (" ..." if len(other) > 6 else ""))
        if sa is None or sb is None:
            ch.status = ADDED if sa is None else REMOVED
            img, addr = (img_b, ch.addr_b) if sa is None else (img_a, ch.addr_a)
            ch.changed_bytes = img.coverage.overlap(addr, addr + ref.size)  # bytes present in the image
            if ch.changed_bytes == 0:
                ch.notes.append("no bytes in the firmware image (e.g. RAM-only .bss)")
            changes.append(ch)
            continue
        ca = _content(img_a, ch.addr_a, sa.size)
        cb = _content(img_b, ch.addr_b, sb.size)
        moved = ch.addr_a != ch.addr_b
        if moved:
            ch.notes.append(f"moved 0x{ch.addr_a:X} -> 0x{ch.addr_b:X}")
        if sa.size != sb.size:
            ch.notes.append(f"size {sa.size:,} -> {sb.size:,} ({sb.size - sa.size:+,} bytes)")
        if ca is None and cb is None:
            # e.g. .bss / RAM-only variables: no bytes in the flash image
            ch.status = MODIFIED if sa.size != sb.size else (MOVED if moved else NOT_IN_IMAGE)
            if ch.status == NOT_IN_IMAGE:
                ch.notes.append("no bytes in the firmware image (RAM-only, e.g. .bss)")
            changes.append(ch)
            continue
        if ca is not None and cb is not None and len(ca) == len(cb):
            if ca == cb:
                ch.status = MOVED if moved else UNCHANGED
                changes.append(ch)
                continue
            real, reloc, unver = _explain_differences(ca, cb, sa.address, sb.address, ra, rb, isa, endian)
            ch.changed_bytes, ch.reloc_bytes, ch.unverified_bytes = real, reloc, unver
            if real == 0 and unver:
                ch.status = LIKELY_RELOCATED
                ch.notes.append(f"{unver} bytes reference unnamed data/code (no symbol) - cannot be verified"
                                + (f"; {reloc} bytes are verified address references" if reloc else ""))
            elif real == 0:
                ch.status = RELOCATED
                ch.notes.append(f"{reloc} bytes are call targets / pointers to code or data that moved")
            else:
                ch.status = MODIFIED
                if reloc:
                    ch.notes.append(f"+{reloc} bytes of address-reference changes")
                if unver:
                    ch.notes.append(f"+{unver} bytes of unverifiable references")
                if not moved:
                    ch.expected_bytes = an.expected_diff.overlap(ch.addr_b, ch.addr_b + sb.size)
        else:
            ch.changed_bytes = count_diff_bytes(ca or b"", cb or b"")
            ch.status = MODIFIED
        changes.append(ch)

    order = {MODIFIED: 0, ADDED: 1, REMOVED: 2, LIKELY_RELOCATED: 3, RELOCATED: 4, MOVED: 5, NOT_IN_IMAGE: 6,
             UNCHANGED: 7}
    changes.sort(key=lambda c: (order[c.status], -c.changed_bytes, c.name))

    modules: dict[str, ModuleChange] = {}
    for c in changes:
        if c.status not in CHANGED_STATUSES:
            continue
        m = modules.setdefault(c.module or "(unknown module)", ModuleChange(c.module or "(unknown module)"))
        if c.status == MODIFIED:
            m.modified += 1
            m.expected_only_symbols += c.expected_only
        elif c.status == ADDED:
            m.added += 1
        elif c.status == REMOVED:
            m.removed += 1
        else:
            m.moved += 1
            continue
        m.changed_bytes += c.changed_bytes
        m.symbols.append(c.name)
    real_modules = [m for m in modules.values() if m.total_changed]
    module_list = sorted(real_modules, key=lambda m: (m.expected_only, -m.total_changed, -m.changed_bytes, m.module))

    sym_ranges = table_b.ranges(vma_b).union(table_a.ranges(vma_a))
    attributed = diff.diff.intersect(sym_ranges).total
    likely = sum(1 for c in changes if c.status == LIKELY_RELOCATED)
    if likely:
        notes.append(f"{likely} symbol(s) differ only in address references, but some references point to unnamed "
                     "data or code (no symbol, e.g. static variables), so they cannot be verified. They are listed "
                     "as LIKELY RELOCATED - review them if in doubt.")
    side = sum(1 for c in changes if c.status in SIDE_EFFECTS)
    if side:
        notes.append(f"{side} symbol(s) only moved or only had call targets / pointers change because other code "
                     "changed size. They are side effects, not edits, and are listed as RELOCATED / MOVED. "
                     "The byte-level summary still counts every shifted byte.")
    if not any(s.module for s in table_b.symbols):
        notes.append("Module (source file) names are unavailable for global ELF symbols; "
                     "a GNU ld .map file provides them for every symbol.")
    if isa is None:
        notes.append(f"Instruction decoding is implemented for ARM/Thumb and MIPS32 only ({arch}); pointer-table "
                     "changes are still recognised, but some calls to moved functions may be reported as MODIFIED.")
    elif isa == "mips":
        notes.append("MIPS32: J/JAL calls, LUI/ADDIU-style address pairs and $gp-relative accesses are decoded. "
                     "microMIPS / MIPS16e code is not decoded and may show address-only changes as MODIFIED.")
    return FeatureReport(table_a.source, table_b.source, changes, module_list, attributed,
                         diff.diff.total - attributed, notes, len(modules) - len(real_modules))
