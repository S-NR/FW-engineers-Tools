"""Self-contained HTML engineering report (no external resources)."""

from __future__ import annotations

import html
import os

from .. import APP_NAME, __version__
from ..core.analysis import ST_CHANGED, ST_EXPECTED, ST_NOT_PRESENT, ST_SAME, V_IDENTICAL, V_UNEXPECTED
from ..core.formatting import addr_width, fmt_addr, fmt_int, fmt_offset, fmt_pct, fmt_size
from ..core.memory_map_model import build_cells
from ..core.metadata_detector import DISCLAIMER

MAX_BLOCK_ROWS = 5000
MAX_SYMBOL_ROWS = 2000

CSS = """
body{font:13px/1.45 "Segoe UI",Arial,sans-serif;color:#1d232a;margin:0;background:#fff}
main{max-width:1200px;margin:0 auto;padding:20px 24px 40px}
h1{font-size:20px;margin:0 0 4px} h2{font-size:15px;margin:28px 0 8px;padding-bottom:4px;border-bottom:2px solid #3b4652}
h3{font-size:13px;margin:16px 0 6px}
.sub{color:#5b6670;font-size:12px}
table{border-collapse:collapse;width:100%;margin:4px 0 8px;font-size:12px}
th,td{border:1px solid #cfd5db;padding:3px 7px;text-align:left;vertical-align:top}
th{background:#e9edf1;font-weight:600}
td.n{text-align:right;font-variant-numeric:tabular-nums}
.mono,td.a{font-family:Consolas,"DejaVu Sans Mono",monospace}
.kv th{width:230px}
.verdict{display:inline-block;padding:6px 14px;font-weight:700;font-size:15px;border:2px solid;margin:10px 0}
.v-ok{color:#1e6b35;border-color:#1e6b35;background:#e7f4ea}
.v-exp{color:#7a5300;border-color:#b07a00;background:#fff4d6}
.v-bad{color:#a01818;border-color:#a01818;background:#fbe6e6}
.s-same{color:#1e6b35;font-weight:600} .s-changed{color:#a01818;font-weight:700}
.s-exp{color:#8a5d00;font-weight:700} .s-np{color:#6b7580}
.note{background:#f4f6f8;border-left:3px solid #8a96a3;padding:6px 10px;margin:8px 0;font-size:12px}
.warn{background:#fff6e0;border-left:3px solid #c98a00;padding:6px 10px;margin:8px 0;font-size:12px}
.map{width:100%;height:auto;border:1px solid #cfd5db}
.legend span{display:inline-block;margin-right:14px;font-size:12px}
.legend i{display:inline-block;width:11px;height:11px;margin-right:4px;vertical-align:-1px;border:1px solid #8a96a3}
footer{margin-top:30px;color:#6b7580;font-size:11px}
@media print{main{max-width:none} h2{page-break-after:avoid} tr{page-break-inside:avoid}}
"""

STATUS_CLASS = {ST_SAME: "s-same", ST_CHANGED: "s-changed", ST_EXPECTED: "s-exp", ST_NOT_PRESENT: "s-np",
                "SAME": "s-same", "CHANGED": "s-changed", "EXPECTED": "s-exp", "MIXED": "s-changed",
                "UNEXPECTED": "s-changed", "ADDED": "s-changed", "REMOVED": "s-changed",
                "CHANGED (EXPECTED)": "s-exp", "UNCHANGED": "s-same", "NOT PRESENT": "s-np",
                "ONLY IN A": "s-changed", "ONLY IN B": "s-changed", "NOT FOUND": "s-np",
                "MODIFIED": "s-changed", "MODIFIED (expected)": "s-exp", "MOVED": "s-np", "RELOCATED": "s-np", "LIKELY RELOCATED": "s-exp"}
MAP_COLORS = {"nodata": "#e3e6ea", "same": "#cfe8d5", "expected": "#f2c14e", "changed": "#d23b3b"}


def e(text) -> str:
    return html.escape(str(text), quote=True)


def _status(text: str) -> str:
    return f'<span class="{STATUS_CLASS.get(text, "")}">{e(text)}</span>'


def _kv_table(rows: list[tuple[str, str]], raw: bool = False) -> str:
    body = "".join(f"<tr><th>{e(k)}</th><td>{v if raw else e(v)}</td></tr>" for k, v in rows)
    return f'<table class="kv">{body}</table>'


def _table(headers: list[str], rows: list[list[str]], numeric: set[int] = frozenset(), addr: set[int] = frozenset()) -> str:
    head = "".join(f"<th>{e(h)}</th>" for h in headers)
    out = []
    for row in rows:
        cells = []
        for i, c in enumerate(row):
            cls = "n" if i in numeric else ("a" if i in addr else "")
            cells.append(f'<td class="{cls}">{c}</td>' if cls else f"<td>{c}</td>")
        out.append("<tr>" + "".join(cells) + "</tr>")
    if not rows:
        out.append(f'<tr><td colspan="{len(headers)}"><i>none</i></td></tr>')
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(out)}</tbody></table>"


def _image_info(img, w: int) -> list[tuple[str, str]]:
    rng = img.address_range
    rows = [
        ("File name", img.name),
        ("Path", os.path.abspath(img.path)),
        ("File size", f"{img.file_size:,} bytes"),
        ("Format", img.format.upper()),
        ("Address range", f"{fmt_addr(rng[0], w)} - {fmt_addr(rng[1], w)}" if rng else "(no data)"),
        ("Firmware data bytes", f"{img.data_size:,}"),
        ("Segments", str(len(img.segments))),
    ]
    m = img.mcu
    if m is not None:
        rows.append(("Controller (heuristic)", m.short()))
        rows += [(f"  {k}", v) for k, v in m.details.items()]
        rows += [("  evidence", f"[{ev.confidence}] {ev.source}: {ev.finding}") for ev in m.evidence]
    rows += list(img.info.items())
    for warn in img.warnings:
        rows.append(("Warning", warn))
    return rows


def _map_svg(session, ncells: int = 512) -> str:
    an, diff = session.analysis, session.diff
    cells, per = build_cells(diff.union, diff.diff, an.expected_diff, ncells)
    if not cells:
        return "<p><i>No data to map.</i></p>"
    cols = 128
    cw, ch = 8, 12
    rows = -(-len(cells) // cols)
    w = addr_width(diff.union.last)
    parts = [f'<svg class="map" viewBox="0 0 {cols * cw} {rows * ch}" xmlns="http://www.w3.org/2000/svg" '
             f'preserveAspectRatio="none" style="max-height:{rows * 14}px">']
    for c in cells:
        x, y = (c.index % cols) * cw, (c.index // cols) * ch
        tip = (f"{fmt_addr(c.start, w)} - {fmt_addr(c.end - 1, w)}: {c.changed:,} changed "
               f"({c.expected:,} expected) of {c.data_bytes:,}")
        parts.append(f'<rect x="{x}" y="{y}" width="{cw - 1}" height="{ch - 1}" fill="{MAP_COLORS[c.status]}">'
                     f"<title>{e(tip)}</title></rect>")
    parts.append("</svg>")
    legend = ('<div class="legend">'
              + "".join(f'<span><i style="background:{MAP_COLORS[k]}"></i>{lbl}</span>'
                        for k, lbl in (("same", "unchanged"), ("expected", "expected change only"),
                                       ("changed", "unexpected change")))
              + f"<span>Each cell = {per:,} bytes of firmware data (gaps without data are omitted).</span></div>")
    return "".join(parts) + legend


def _block_rows(blocks, session, w) -> list[list[str]]:
    a, b = session.image_a, session.image_b
    return [[str(blk.index), fmt_addr(blk.start, w), fmt_addr(blk.last, w), fmt_int(blk.size),
             fmt_int(blk.changed_bytes), fmt_int(blk.expected_bytes), fmt_int(blk.unexpected_bytes),
             _status(blk.classification), e(blk.region_label), e(blk.kind),
             e(fmt_offset(a.file_offset_of(blk.start))), e(fmt_offset(b.file_offset_of(blk.start))),
             e(session.symbol_label(blk.start) or "-")]
            for blk in blocks]


BLOCK_HEADERS = ["#", "Start", "End", "Size", "Changed", "Expected", "Unexpected", "Class", "Region",
                 "Kind", "File offset A", "File offset B", "Symbol"]


def _features_section(session, w) -> str:
    out = ["<h2>Changed functions and modules (feature level)</h2>"]
    fr = session.features
    if fr is None:
        msg = session.features_error or (
            "No symbol information available. Compare ELF files, or supply the ELF / GNU ld .map file of each "
            "build (GUI: 'Changed Functions' tab; CLI: --symbols-a / --symbols-b), to see which functions, "
            "variables and source modules changed.")
        out.append(f"<div class='note'>{e(msg)}</div>")
        return "".join(out)
    out.append(f"<p>Symbols: <b>{e(fr.source_a)}</b> &rarr; <b>{e(fr.source_b)}</b>. "
               f"<b>{e(fr.headline())}</b>. Functions are compared by content; calls and pointers to code "
               f"that merely moved are decoded, so functions changed only by such address updates are "
               f"RELOCATED (side effect), not MODIFIED.</p>")
    for note in fr.notes:
        out.append(f"<div class='warn'>{e(note)}</div>")
    out.append("<h3>By module (source / object file)</h3>")
    out.append(_table(["Module", "Modified", "Added", "Removed", "Changed bytes", "Status", "Symbols"],
                      [[e(m.module), fmt_int(m.modified), fmt_int(m.added), fmt_int(m.removed),
                        fmt_int(m.changed_bytes), _status("EXPECTED" if m.expected_only else "CHANGED"),
                        e(", ".join(m.symbols))] for m in fr.modules], numeric={1, 2, 3, 4}))
    out.append("<h3>Functions / variables</h3>")
    rows = []
    for c in fr.changed[:MAX_SYMBOL_ROWS]:
        notes = (["expected change"] if c.expected_only else []) + c.notes
        status = "MODIFIED (expected)" if c.expected_only else c.status
        rows.append([e(c.name), e(c.kind), e(c.module or "-"), _status(status),
                     fmt_addr(c.addr_a, w), fmt_addr(c.addr_b, w), fmt_int(c.size_a), fmt_int(c.size_b),
                     fmt_int(c.changed_bytes), e("; ".join(notes))])
    out.append(_table(["Symbol", "Kind", "Module", "Status", "Addr A", "Addr B", "Size A", "Size B",
                       "Changed bytes", "Notes"], rows, numeric={6, 7, 8}, addr={4, 5}))
    if fr.uncertain:
        out.append(f"<h3>Likely relocated ({len(fr.uncertain):,}) - review</h3><p>Only address references differ, "
                   "but some point to unnamed data or code (no symbol), so equivalence cannot be proven.</p>")
        out.append(_table(["Symbol", "Kind", "Module", "Addr A", "Addr B", "Unverified bytes", "Notes"],
                          [[e(c.name), e(c.kind), e(c.module or "-"), fmt_addr(c.addr_a, w), fmt_addr(c.addr_b, w),
                            fmt_int(c.unverified_bytes), e("; ".join(c.notes))]
                           for c in fr.uncertain[:MAX_SYMBOL_ROWS]], numeric={5}, addr={3, 4}))
    side = fr.side_effects
    if side:
        out.append(f"<details><summary>{len(side):,} symbol(s) changed only as a side effect "
                   f"(relocated / moved)</summary>")
        out.append(_table(["Symbol", "Kind", "Module", "Status", "Addr A", "Addr B", "Notes"],
                          [[e(c.name), e(c.kind), e(c.module or "-"), _status(c.status), fmt_addr(c.addr_a, w),
                            fmt_addr(c.addr_b, w), e("; ".join(c.notes))] for c in side[:MAX_SYMBOL_ROWS]],
                          addr={4, 5}))
        out.append("</details>")
    if fr.unattributed_bytes:
        out.append(f"<p>{fr.unattributed_bytes:,} changed bytes lie outside every known symbol "
                   "(padding, literal pools, unnamed data).</p>")
    return "".join(out)


def render_html_report(session) -> str:
    an, diff, cfg = session.analysis, session.diff, session.config
    a, b = session.image_a, session.image_b
    w = addr_width(diff.union.last)
    ts = session.timestamp.strftime("%Y-%m-%d %H:%M:%S %Z").strip()
    verdict = an.verdict
    vcls = "v-ok" if verdict == V_IDENTICAL else ("v-bad" if verdict == V_UNEXPECTED else "v-exp")
    out: list[str] = []
    out.append(f"<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
               f"<title>Firmware comparison - {e(a.name)} vs {e(b.name)}</title><style>{CSS}</style></head><body><main>")
    out.append(f"<h1>Firmware Binary Comparison Report</h1>"
               f"<div class='sub'>{e(a.name)} &rarr; {e(b.name)} &middot; generated {e(ts)} &middot; "
               f"{e(APP_NAME)} {e(__version__)}</div>")
    out.append(f"<div class='verdict {vcls}'>{e(verdict)}</div>")
    out.append("<div class='note'>Addresses are MCU memory addresses. File offsets (shown with a '+' prefix) are "
               "byte positions inside the file on disk; Intel HEX files have no byte-level file offset (n/a).</div>")
    for warn in an.config_warnings:
        out.append(f"<div class='warn'>Configuration warning: {e(warn)}</div>")
    if session.controller_mismatch:
        out.append(f"<div class='warn'><b>Firmware A and B appear to target different controllers:</b> "
                   f"{e(a.mcu.short())} vs {e(b.mcu.short())}</div>")

    out.append("<h2>1. Firmware A (old)</h2>" + _kv_table(_image_info(a, w)))
    out.append("<h2>2. Firmware B (new)</h2>" + _kv_table(_image_info(b, w)))

    match = a.sha256 == b.sha256
    out.append("<h2>3. SHA-256 hashes (whole file)</h2>" + _kv_table([
        ("Firmware A", f"<span class='mono'>{e(a.sha256)}</span>"),
        ("Firmware B", f"<span class='mono'>{e(b.sha256)}</span>"),
        ("Files identical", _status("SAME") if match else _status("CHANGED")),
    ], raw=True))

    lb = an.largest_block
    summary = [
        ("Bytes compared", fmt_int(diff.total_compared)),
        ("Gap handling", "strict: bytes present in only one image are differences" if diff.fill is None
         else f"one-sided bytes compared against fill value 0x{diff.fill:02X}"),
        ("Address space covered (A ∪ B)", fmt_int(diff.union_bytes)),
        ("Different bytes (total)", fmt_int(diff.diff_bytes)),
        ("&nbsp;&nbsp;modified (present in both)", fmt_int(diff.content_diff_bytes)),
        ("&nbsp;&nbsp;present only in A", fmt_int(diff.only_a_bytes)),
        ("&nbsp;&nbsp;present only in B", fmt_int(diff.only_b_bytes)),
        ("Changed", f"{diff.changed_percent:.2f}%"),
        ("First differing address", _addr_with_offsets(diff.first_diff, a, b, w)),
        ("Last differing address", _addr_with_offsets(diff.last_diff, a, b, w)),
        ("Changed blocks", f"{len(an.blocks):,}" + (f" (merge gap {cfg.merge_gap} bytes)" if cfg.merge_gap else "")),
        ("Largest block", f"{lb.size:,} bytes at {fmt_addr(lb.start, w)}" if lb else "-"),
        ("Expected changed bytes", fmt_int(an.expected_bytes)),
        ("Unexpected changed bytes", fmt_int(an.unexpected_bytes)),
    ]
    body = "".join(f"<tr><th>{k}</th><td>{e(v)}</td></tr>" for k, v in summary)
    out.append(f"<h2>4. Overall comparison summary</h2><table class='kv'>{body}</table>")
    out.append("<h3>Memory map</h3>" + _map_svg(session))

    out.append("<h2>5. Region comparison</h2>")
    if cfg.regions:
        rows = [[e(r.region.name), fmt_addr(r.region.start, w), fmt_addr(r.region.end, w), e(fmt_size(r.size)),
                 fmt_int(r.changed_bytes), f"{r.changed_percent:.2f}%", fmt_int(r.expected_bytes),
                 fmt_int(r.unexpected_bytes), e(r.coverage_note), _status(r.status)] for r in an.region_results]
        if an.outside_changed:
            rows.append([_status("(outside configured regions)"), "", "", "", fmt_int(an.outside_changed), "",
                         fmt_int(an.outside_expected), fmt_int(an.outside_changed - an.outside_expected), "",
                         _status(ST_CHANGED)])
        out.append(_table(["Region", "Start", "End", "Size", "Changed bytes", "Changed %", "Expected",
                           "Unexpected", "Coverage", "Status"], rows, numeric={4, 5, 6, 7}, addr={1, 2}))
    else:
        out.append("<p><i>No memory regions configured.</i></p>")

    out.append("<h2>6. Changed blocks</h2>")
    shown = an.blocks[:MAX_BLOCK_ROWS]
    if len(an.blocks) > len(shown):
        out.append(f"<div class='warn'>Showing the first {len(shown):,} of {len(an.blocks):,} blocks. "
                   "Use the CSV export for the complete list.</div>")
    out.append(_table(BLOCK_HEADERS, _block_rows(shown, session, w), numeric={0, 3, 4, 5, 6}, addr={1, 2, 10, 11}))

    out.append("<h2>7. Expected / ignored changes</h2>")
    if cfg.expected:
        rows = [[e(r.area.name), fmt_addr(r.area.start, w), fmt_addr(r.area.end, w), fmt_int(r.area.size),
                 fmt_int(r.changed_bytes), e(r.area.description), _status(r.status)] for r in an.expected_results]
        out.append(_table(["Expected area", "Start", "End", "Size", "Changed bytes", "Description", "Status"],
                          rows, numeric={3, 4}, addr={1, 2}))
        out.append(f"<p>Total expected changed bytes: <b>{an.expected_bytes:,}</b></p>")
    else:
        out.append("<p><i>No expected-difference areas configured - every difference is treated as unexpected.</i></p>")

    out.append("<h2>8. Unexpected changes</h2>")
    unexp = an.unexpected_blocks
    if unexp:
        out.append(f"<p><b class='s-changed'>{an.unexpected_bytes:,} unexpected changed bytes in "
                   f"{len(unexp):,} block(s).</b></p>")
        shown = unexp[:MAX_BLOCK_ROWS]
        if len(unexp) > len(shown):
            out.append(f"<div class='warn'>Showing the first {len(shown):,} of {len(unexp):,} blocks.</div>")
        out.append(_table(BLOCK_HEADERS, _block_rows(shown, session, w), numeric={0, 3, 4, 5, 6}, addr={1, 2, 10, 11}))
    else:
        out.append("<p class='s-same'>No unexpected changes.</p>")

    out.append(_features_section(session, w))

    out.append("<h2>9. Metadata differences</h2>")
    out.append(f"<div class='warn'>{e(DISCLAIMER)}</div>")
    if session.metadata_diff:
        rows = [[e(m.category), e(", ".join(m.values_a) or "-"), e(", ".join(m.values_b) or "-"), _status(m.status)]
                for m in session.metadata_diff]
        out.append(_table(["Item", "Firmware A", "Firmware B", "Status"], rows))
    else:
        out.append("<p><i>Metadata detection was not run.</i></p>")

    if session.elf is not None:
        out.append(_elf_section(session, w))
    elif session.elf_error:
        out.append(f"<h2>ELF comparison</h2><div class='warn'>{e(session.elf_error)}</div>")

    out.append("<h2>10. Comparison timestamp</h2>")
    out.append(_kv_table([("Compared at", ts), ("Comparison time", f"{diff.elapsed:.3f} s"),
                          ("Configuration", cfg.name or "(unnamed)"),
                          ("Regions / expected areas", f"{len(cfg.regions)} / {len(cfg.expected)}")]))
    out.append(f"<footer>Generated by {e(APP_NAME)} {e(__version__)}. Original firmware files were opened "
               f"read-only and were not modified.</footer></main></body></html>")
    return "".join(out)


def _addr_with_offsets(addr, a, b, w) -> str:
    if addr is None:
        return "-"
    return (f"{fmt_addr(addr, w)}  (file offset A {fmt_offset(a.file_offset_of(addr))}, "
            f"B {fmt_offset(b.file_offset_of(addr))})")


def _elf_section(session, w) -> str:
    elf = session.elf
    out = ["<h2>ELF section comparison</h2>"]
    rows = [[e(s.name), e(s.type_name), e(s.flags), fmt_addr(s.addr_a, w), fmt_addr(s.addr_b, w),
             fmt_int(s.size_a), fmt_int(s.size_b), fmt_int(s.diff_bytes), _status(s.status), e("; ".join(s.notes))]
            for s in elf.sections]
    out.append(_table(["Section", "Type", "Flags", "Addr A", "Addr B", "Size A", "Size B", "Diff bytes",
                       "Status", "Notes"], rows, numeric={5, 6, 7}, addr={3, 4}))
    keys = list(dict.fromkeys(list(elf.build_info_a) + list(elf.build_info_b)))
    if keys:
        out.append("<h3>Build information</h3>")
        out.append(_table(["Item", "Firmware A", "Firmware B"],
                          [[e(k), e(elf.build_info_a.get(k, "-")), e(elf.build_info_b.get(k, "-"))] for k in keys]))
    out.append(f"<h3>Symbol changes ({len(elf.symbols):,}; symbols A {elf.symbol_count_a:,} / "
               f"B {elf.symbol_count_b:,})</h3>")
    syms = elf.symbols[:MAX_SYMBOL_ROWS]
    if len(elf.symbols) > len(syms):
        out.append(f"<div class='warn'>Showing first {len(syms):,} symbol changes.</div>")
    out.append(_table(["Symbol", "Change", "Type", "Addr A", "Addr B", "Size A", "Size B"],
                      [[e(s.name), e(s.status), e(s.type), fmt_addr(s.addr_a, w), fmt_addr(s.addr_b, w),
                        fmt_int(s.size_a), fmt_int(s.size_b)] for s in syms], numeric={5, 6}, addr={3, 4}))
    return "".join(out)


def write_html_report(session, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_html_report(session))
