"""CSV export of changed blocks."""

from __future__ import annotations

import csv

from ..core.formatting import addr_width

HEADER = [
    "block", "start_address", "end_address", "size_bytes", "changed_bytes",
    "only_in_a_bytes", "only_in_b_bytes", "expected_bytes", "unexpected_bytes",
    "classification", "regions", "file_offset_a", "file_offset_b", "symbol",
]

SYMBOL_HEADER = ["status", "kind", "symbol", "module", "section", "address_a", "address_b", "size_a", "size_b",
                 "size_delta", "changed_bytes", "address_ref_bytes", "expected_only", "notes"]


def write_blocks_csv(session, path: str, blocks=None) -> int:
    """Write changed blocks to *path*; returns the number of rows written."""
    an = session.analysis
    blocks = an.blocks if blocks is None else blocks
    rng = session.diff.union.last
    w = addr_width(rng)
    a, b = session.image_a, session.image_b

    def off(img, addr):
        o = img.file_offset_of(addr)
        return "" if o is None else f"0x{o:X}"

    with open(path, "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(HEADER)
        for blk in blocks:
            wr.writerow([
                blk.index, f"0x{blk.start:0{w}X}", f"0x{blk.last:0{w}X}", blk.size, blk.changed_bytes,
                blk.only_a_bytes, blk.only_b_bytes, blk.expected_bytes, blk.unexpected_bytes,
                blk.classification, ";".join(blk.regions), off(a, blk.start), off(b, blk.start),
                session.symbol_label(blk.start),
            ])
    return len(blocks)


def write_symbols_csv(session, path: str, include_unchanged: bool = False) -> int:
    """Write the function / variable comparison; returns the number of rows."""
    fr = session.features
    rows = [] if fr is None else (fr.symbols if include_unchanged else fr.changed + fr.uncertain + fr.side_effects)

    def hx(v):
        return "" if v is None else f"0x{v:08X}"

    with open(path, "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(SYMBOL_HEADER)
        for c in rows:
            wr.writerow([c.status, c.kind, c.name, c.module, c.section, hx(c.addr_a), hx(c.addr_b),
                         "" if c.size_a is None else c.size_a, "" if c.size_b is None else c.size_b,
                         c.size_delta, c.changed_bytes, c.reloc_bytes, "yes" if c.expected_only else "",
                         "; ".join(c.notes)])
    return len(rows)
