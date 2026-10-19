"""Comparison summary, region comparison table and changed-block table."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..core.analysis import V_IDENTICAL, V_UNEXPECTED
from ..core.block_detector import sort_blocks
from ..core.formatting import addr_width, fmt_addr, fmt_int, fmt_offset, fmt_size, parse_int
from . import theme
from .widgets import DataTable, KeyValueGrid

SUMMARY_KEYS = ["Total compared", "Different bytes", "Changed", "Changed blocks", "Largest block",
                "First difference", "Last difference", "Only in A", "Only in B",
                "Expected changes", "Unexpected changes", "Gap handling", "Functions changed"]


VERDICT_TEXT = {
    V_IDENTICAL: "The two firmware images are identical.",
    "EXPECTED CHANGES ONLY": "Every difference lies inside an expected-difference area (version, timestamp, CRC…).",
    V_UNEXPECTED: "Differences were found outside the configured expected-difference areas.",
}
TILES = [("diff", "Different bytes"), ("pct", "Changed"), ("blocks", "Changed blocks"),
         ("unexp", "Unexpected bytes"), ("funcs", "Functions changed")]


class _Tile(tk.Frame):
    """Big number with a caption."""

    def __init__(self, master, caption: str):
        super().__init__(master, background=theme.CARD, highlightthickness=1, highlightbackground=theme.BORDER,
                         padx=14, pady=10)
        self.value = tk.Label(self, text="–", font=theme.kpi_font(), background=theme.CARD, foreground=theme.FG,
                              anchor="w")
        self.value.pack(fill="x")
        self.caption = tk.Label(self, text=caption, font=theme.small_font(), background=theme.CARD,
                                foreground=theme.MUTED, anchor="w")
        self.caption.pack(fill="x")

    def set(self, value: str, color: str = theme.FG, caption: str | None = None) -> None:
        self.value.configure(text=value, foreground=color)
        if caption is not None:
            self.caption.configure(text=caption)


class SummaryPanel(ttk.Frame):
    def __init__(self, master):
        super().__init__(master)
        # verdict banner
        self.banner = tk.Frame(self, padx=18, pady=12)
        self.banner.pack(fill="x")
        self.icon = tk.Label(self.banner, font=theme.kpi_font())
        self.icon.pack(side="left", padx=(0, 14))
        text = tk.Frame(self.banner)
        text.pack(side="left", fill="x", expand=True)
        self.verdict = tk.Label(text, font=theme.big_font(), anchor="w")
        self.verdict.pack(fill="x")
        self.explain = tk.Label(text, anchor="w", justify="left")
        self.explain.pack(fill="x")
        self.detail = tk.Label(self.banner, font=theme.small_font(), anchor="e")
        self.detail.pack(side="right")
        self._banner_parts = (self.banner, self.icon, text, self.verdict, self.explain, self.detail)
        # number tiles
        tiles = ttk.Frame(self)
        tiles.pack(fill="x", pady=(12, 0))
        self.tiles = {}
        for i, (key, caption) in enumerate(TILES):
            t = _Tile(tiles, caption)
            t.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 10, 0))
            tiles.columnconfigure(i, weight=1, uniform="tile")
            self.tiles[key] = t
        # details
        details = ttk.LabelFrame(self, text="  Details  ", padding=(12, 6))
        details.pack(fill="x", pady=(12, 0))
        self.grid_kv = KeyValueGrid(details, SUMMARY_KEYS, columns=3, value_width=28)
        self.grid_kv.pack(fill="x")
        self.clear()

    def _paint_banner(self, kind: str, title: str, explanation: str, detail: str = "") -> None:
        fg, bg, icon = theme.VERDICT_STYLE.get(kind, theme.VERDICT_STYLE["NONE"])
        for w in self._banner_parts:
            w.configure(background=bg)
        self.icon.configure(text=icon, foreground=fg)
        self.verdict.configure(text=title, foreground=fg)
        self.explain.configure(text=explanation, foreground=fg)
        self.detail.configure(text=detail, foreground=fg)

    def clear(self, message: str = "Load Firmware A and Firmware B on the Files tab - the comparison runs "
                                   "automatically.") -> None:
        self._paint_banner("NONE", "No comparison yet", message)
        for t in self.tiles.values():
            t.set("–", theme.FAINT)
        self.grid_kv.clear()

    def show(self, session) -> None:
        an, diff = session.analysis, session.diff
        a, b = session.image_a, session.image_b
        w = addr_width(diff.union.last)
        explanation = VERDICT_TEXT.get(an.verdict, "")
        if an.verdict == V_UNEXPECTED and not an.config.expected:
            explanation = ("The images differ. No expected-difference areas are configured, so every difference "
                           "counts as unexpected.")
        self._paint_banner(an.verdict, an.verdict, explanation,
                           f"compared {session.timestamp:%Y-%m-%d %H:%M:%S}\nin {diff.elapsed:.2f} s")
        bad = theme.C_CHANGED if diff.diff_bytes else theme.C_SAME
        self.tiles["diff"].set(f"{diff.diff_bytes:,}", bad)
        self.tiles["pct"].set(f"{diff.changed_percent:.2f}%", bad)
        self.tiles["blocks"].set(f"{len(an.blocks):,}", bad)
        self.tiles["unexp"].set(f"{an.unexpected_bytes:,}", theme.C_CHANGED if an.unexpected_bytes else theme.C_SAME,
                                f"Unexpected bytes ({an.expected_bytes:,} expected)" if an.config.expected
                                else "Unexpected bytes (no expected areas)")
        fr = session.features
        if fr is None:
            self.tiles["funcs"].set("–", theme.FAINT, "Functions changed (load ELF / .map)")
        else:
            real = len(fr.changed)
            self.tiles["funcs"].set(f"{real:,}", theme.C_CHANGED if real else theme.C_SAME,
                                    f"Functions changed (+{len(fr.side_effects):,} only moved)")

        def where(addr):
            if addr is None:
                return "-"
            return (f"{fmt_addr(addr, w)} (A {fmt_offset(a.file_offset_of(addr))}"
                    f" / B {fmt_offset(b.file_offset_of(addr))})")

        lb = an.largest_block
        g = self.grid_kv
        g.set("Total compared", f"{diff.total_compared:,} bytes")
        g.set("Different bytes", f"{diff.diff_bytes:,}")
        g.set("Changed", f"{diff.changed_percent:.2f}%")
        g.set("Changed blocks", f"{len(an.blocks):,}" + (f" (gap ≤ {an.config.merge_gap})" if an.config.merge_gap else ""))
        g.set("Largest block", f"{lb.size:,} bytes @ {fmt_addr(lb.start, w)}" if lb else "-")
        g.set("First difference", where(diff.first_diff))
        g.set("Last difference", where(diff.last_diff))
        g.set("Only in A", f"{diff.only_a_bytes:,} bytes")
        g.set("Only in B", f"{diff.only_b_bytes:,} bytes")
        g.set("Expected changes", f"{an.expected_bytes:,} bytes" if an.config.expected else "none configured")
        g.set("Unexpected changes", f"{an.unexpected_bytes:,} bytes")
        g.set("Gap handling", "strict (one-sided = diff)" if diff.fill is None else f"fill 0x{diff.fill:02X}")
        fr = session.features
        g.set("Functions changed", "no symbols (load ELF / .map)" if fr is None else
              f"{fr.count('MODIFIED')} mod, {fr.count('ADDED')} add, {fr.count('REMOVED')} rem "
              f"(+{fr.count('RELOCATED') + fr.count('MOVED')} relocated/moved)")


REGION_COLUMNS = [
    ("region", "Region", 150, "w"), ("start", "Start", 110, "w"), ("end", "End", 110, "w"),
    ("size", "Size", 80, "e"), ("changed", "Changed Bytes", 105, "e"), ("pct", "Changed %", 80, "e"),
    ("expected", "Expected", 80, "e"), ("unexpected", "Unexpected", 85, "e"),
    ("coverage", "Coverage", 130, "w"), ("status", "Status", 130, "w"),
]


class RegionTable(ttk.Frame):
    def __init__(self, master, on_open_address, on_configure=None):
        super().__init__(master, padding=(14, 12))
        self.on_open_address = on_open_address
        self.session = None
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 8))
        ttk.Label(bar, text="Per-region comparison.  Double-click a region to inspect it in the hex viewer.",
                  style="Muted.TLabel").pack(side="left")
        if on_configure is not None:
            ttk.Button(bar, text="Configure regions…", command=on_configure).pack(side="right")
        self.table = DataTable(self, REGION_COLUMNS, height=8)
        self.table.pack(fill="both", expand=True)
        self.table.tree.bind("<Double-1>", self._open)
        self.warn = ttk.Label(self, style="Warn.TLabel", wraplength=1000, justify="left")
        self.warn.pack(fill="x")

    def show(self, session) -> None:
        self.session = session
        if session is None:
            self.table.clear()
            self.warn.configure(text="")
            return
        an = session.analysis
        w = addr_width(session.diff.union.last)
        self.table.empty_text = ("No memory regions configured yet.  Use 'Configure regions…' to define ranges such "
                                 "as Bootloader / Application / Calibration, or load a project file - the change "
                                 "statistics are then shown per region.")
        rows = []
        for i, r in enumerate(an.region_results):
            rows.append((f"r{i}", (r.region.name, fmt_addr(r.region.start, w), fmt_addr(r.region.end, w),
                                   fmt_size(r.size), fmt_int(r.changed_bytes), f"{r.changed_percent:.2f}%",
                                   fmt_int(r.expected_bytes), fmt_int(r.unexpected_bytes), r.coverage_note, r.status),
                         (r.status,),
                         (r.region.name, r.region.start, r.region.end, r.size, r.changed_bytes, r.changed_percent,
                          r.expected_bytes, r.unexpected_bytes, r.coverage_note, r.status)))
        if an.config.regions and an.outside_changed:
            rows.append(("outside", ("(outside configured regions)", "", "", "", fmt_int(an.outside_changed), "",
                                     fmt_int(an.outside_expected), fmt_int(an.outside_changed - an.outside_expected),
                                     "", "CHANGED"), ("CHANGED",),
                         ("~", None, None, None, an.outside_changed, None, an.outside_expected, None, "", "CHANGED")))
        self.table.set_rows(rows)
        self.warn.configure(text="\n".join(an.config_warnings))

    def _open(self, _event=None) -> None:
        iid = self.table.selected_iid()
        if not iid or self.session is None:
            return
        diff = self.session.diff
        if iid == "outside":
            regions = self.session.config.regions
            for s, e in diff.diff:
                if not any(r.start <= s <= r.end for r in regions):
                    self.on_open_address(s)
                    return
            return
        r = self.session.analysis.region_results[int(iid[1:])].region
        first = diff.diff.first_at_or_after(r.start)
        self.on_open_address(first if first is not None and first <= r.end else r.start)


BLOCK_COLUMNS = [
    ("idx", "#", 55, "e"), ("start", "Start", 110, "w"), ("end", "End", 110, "w"), ("size", "Size", 85, "e"),
    ("changed", "Changed", 80, "e"), ("expected", "Expected", 75, "e"), ("unexpected", "Unexpected", 85, "e"),
    ("class", "Class", 95, "w"), ("region", "Region", 140, "w"), ("kind", "Kind", 120, "w"),
    ("offa", "File off. A", 90, "e"), ("offb", "File off. B", 90, "e"),
    ("symbol", "Function / symbol", 220, "w"),
]
SORTS = {"Address": "address", "Size": "size", "Changed bytes": "changed", "Region": "region"}
CLASS_FILTERS = ["All blocks", "Unexpected only", "Expected only"]
ALL = "(all regions)"
ROW_LIMIT = 20000


class BlockTable(ttk.Frame):
    def __init__(self, master, on_open_address, on_export_csv):
        super().__init__(master, padding=(14, 12))
        self.on_open_address = on_open_address
        self.session = None
        self._shown = []
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 4))
        ttk.Label(bar, text="Sort by").pack(side="left")
        self.sort_box = ttk.Combobox(bar, values=list(SORTS), state="readonly", width=14)
        self.sort_box.set("Address")
        self.sort_box.pack(side="left", padx=(3, 10))
        ttk.Label(bar, text="Show").pack(side="left")
        self.class_box = ttk.Combobox(bar, values=CLASS_FILTERS, state="readonly", width=15)
        self.class_box.set(CLASS_FILTERS[0])
        self.class_box.pack(side="left", padx=(3, 10))
        ttk.Label(bar, text="Region").pack(side="left")
        self.region_box = ttk.Combobox(bar, values=[ALL], state="readonly", width=18)
        self.region_box.set(ALL)
        self.region_box.pack(side="left", padx=(3, 10))
        ttk.Label(bar, text="Size >").pack(side="left")
        self.min_var = tk.StringVar(value="0")
        ent = ttk.Entry(bar, textvariable=self.min_var, width=8, font=theme.mono_font())
        ent.pack(side="left", padx=(3, 3))
        ttk.Label(bar, text="bytes").pack(side="left", padx=(0, 10))
        for w in (self.sort_box, self.class_box, self.region_box):
            w.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        ent.bind("<Return>", lambda e: self.refresh())
        ttk.Button(bar, text="Apply", command=self.refresh).pack(side="left")
        ttk.Button(bar, text="Export CSV…", command=on_export_csv).pack(side="right")
        ttk.Button(bar, text="Open in hex viewer", command=self._open).pack(side="right", padx=4)
        self.error = ttk.Label(bar, style="Warn.TLabel")
        self.error.pack(side="left", padx=8)
        self.table = DataTable(self, BLOCK_COLUMNS, height=10, row_limit=ROW_LIMIT)
        self.table.pack(fill="both", expand=True)
        self.table.tree.bind("<Double-1>", self._open)

    def show(self, session) -> None:
        self.session = session
        names = [ALL] + ([r.name for r in session.config.regions] + ["(outside regions)"] if session else [])
        self.region_box.configure(values=names)
        if self.region_box.get() not in names:
            self.region_box.set(ALL)
        self.refresh()

    def filtered_blocks(self):
        if self.session is None:
            return []
        try:
            min_size = parse_int(self.min_var.get() or "0")
            self.error.configure(text="")
        except ValueError as exc:
            self.error.configure(text=f"Size filter: {exc}")
            min_size = 0
        blocks = self.session.analysis.blocks
        cls = self.class_box.get()
        region = self.region_box.get()
        out = []
        for b in blocks:
            if b.size <= min_size and min_size:
                continue
            if cls == "Unexpected only" and b.unexpected_bytes == 0:
                continue
            if cls == "Expected only" and b.expected_bytes == 0:
                continue
            if region != ALL:
                if region == "(outside regions)":
                    if b.regions:
                        continue
                elif region not in b.regions:
                    continue
            out.append(b)
        return sort_blocks(out, SORTS[self.sort_box.get()])

    def refresh(self) -> None:
        if self.session is None:
            self.table.clear()
            return
        a, b = self.session.image_a, self.session.image_b
        w = addr_width(self.session.diff.union.last)
        self.table.empty_text = ("No differences - the two images are identical." if not self.session.analysis.blocks
                                 else "No blocks match the current filter.")
        blocks = self.filtered_blocks()
        self._shown = blocks
        rows = []
        for blk in blocks[:ROW_LIMIT]:
            oa, ob = a.file_offset_of(blk.start), b.file_offset_of(blk.start)
            sym = self.session.symbol_label(blk.start)
            rows.append((str(blk.index),
                         (blk.index, fmt_addr(blk.start, w), fmt_addr(blk.last, w), fmt_int(blk.size),
                          fmt_int(blk.changed_bytes), fmt_int(blk.expected_bytes), fmt_int(blk.unexpected_bytes),
                          blk.classification, blk.region_label, blk.kind, fmt_offset(oa), fmt_offset(ob),
                          sym or "-"),
                         (blk.classification,),
                         (blk.index, blk.start, blk.last, blk.size, blk.changed_bytes, blk.expected_bytes,
                          blk.unexpected_bytes, blk.classification, blk.region_label, blk.kind, oa, ob, sym)))
        self.table._sort_state = None
        for cid, heading, _, _ in BLOCK_COLUMNS:
            self.table.tree.heading(cid, text=heading)
        self.table.row_limit = ROW_LIMIT
        self.table.set_rows(rows, total=len(blocks))
        if len(blocks) > ROW_LIMIT:
            self.table.notice.configure(text=f"Showing {ROW_LIMIT:,} of {len(blocks):,} blocks - refine the "
                                             f"filter or use Export CSV for the complete list.")
        else:
            self.table.notice.configure(text=f"{len(blocks):,} of {len(self.session.analysis.blocks):,} blocks shown")

    def _open(self, _event=None) -> None:
        iid = self.table.selected_iid()
        if iid and self.session is not None:
            blk = self.session.analysis.blocks[int(iid) - 1]
            self.on_open_address(blk.start)
