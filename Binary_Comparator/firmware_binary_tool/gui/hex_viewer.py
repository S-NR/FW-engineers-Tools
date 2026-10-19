"""Synchronised, virtualised hex comparison viewer.

Columns: Address | file offset A | file offset B | Firmware A | Firmware B | Status.
Only the rows visible on screen are rendered.
"""

from __future__ import annotations

import tkinter as tk
from bisect import bisect_right
from tkinter import ttk

from ..core.binary_comparator import DIFF, NO_DATA, ONLY_A, ONLY_B, SAME, SAME_FILL
from ..core.formatting import addr_width, fmt_addr, fmt_offset, parse_hex_bytes, parse_int
from . import theme
from .hex_model import FILTER_ALL, FILTER_BLOCKS, FILTERS, HexRowModel, build_view_intervals

ALL_REGIONS = "(all regions)"
ROW_SIZES = {"1 byte/row (table)": 1, "8 bytes/row": 8, "16 bytes/row": 16, "32 bytes/row": 32}
GOTO_MODES = ["Address", "File offset A", "File offset B"]
SEARCH_SIDES = ["A", "B", "A or B"]


class HexComparisonWindow(tk.Toplevel):
    def __init__(self, master, session, start_addr: int | None = None):
        super().__init__(master)
        self.title("Hex Comparison")
        self.geometry("1280x720")
        self.minsize(800, 400)
        self.configure(background=theme.BG)
        self.session = None
        self.model: HexRowModel | None = None
        self.top_row = 0
        self.visible_lines = 30
        self.cursor: int | None = None
        self.match: tuple[int, int] | None = None  # (address, length) of last search hit
        self._layout: dict = {}
        self.bpr = 16
        self.aw = 8
        self._block_starts: list[int] = []
        self._build_ui()
        self.set_session(session, start_addr)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        nav = ttk.Frame(self, padding=(10, 8, 10, 2))
        nav.pack(fill="x")
        for text, cmd in (("|◀ First diff", self.first_diff), ("◀ Prev diff", self.prev_diff),
                          ("Next diff ▶", self.next_diff), ("Last diff ▶|", self.last_diff)):
            ttk.Button(nav, text=text, command=cmd, style="Tool.TButton").pack(side="left", padx=(0, 3))
        ttk.Separator(nav, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Label(nav, text="Go to").pack(side="left")
        self.goto_mode = ttk.Combobox(nav, values=GOTO_MODES, state="readonly", width=11)
        self.goto_mode.set(GOTO_MODES[0])
        self.goto_mode.pack(side="left", padx=3)
        self.goto_var = tk.StringVar()
        goto = ttk.Entry(nav, textvariable=self.goto_var, width=14, font=theme.mono_font())
        goto.pack(side="left")
        goto.bind("<Return>", lambda e: self.goto())
        ttk.Button(nav, text="Go", command=self.goto, width=4, style="Tool.TButton").pack(side="left", padx=3)
        ttk.Separator(nav, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Label(nav, text="Search hex").pack(side="left")
        self.search_var = tk.StringVar()
        se = ttk.Entry(nav, textvariable=self.search_var, width=18, font=theme.mono_font())
        se.pack(side="left", padx=3)
        se.bind("<Return>", lambda e: self.search(False))
        ttk.Label(nav, text="in").pack(side="left")
        self.search_side = ttk.Combobox(nav, values=SEARCH_SIDES, state="readonly", width=6)
        self.search_side.set("A or B")
        self.search_side.pack(side="left", padx=3)
        ttk.Button(nav, text="◀ Find", command=lambda: self.search(True), style="Tool.TButton").pack(side="left", padx=(3, 0))
        ttk.Button(nav, text="Find ▶", command=lambda: self.search(False), style="Tool.TButton").pack(side="left", padx=3)
        self._goto_entry = goto

        view = ttk.Frame(self, padding=(10, 4, 10, 6))
        view.pack(fill="x")
        ttk.Label(view, text="View").pack(side="left")
        self.filter_box = ttk.Combobox(view, values=FILTERS, state="readonly", width=24)
        self.filter_box.set(FILTER_ALL)
        self.filter_box.pack(side="left", padx=3)
        self.filter_box.bind("<<ComboboxSelected>>", lambda e: self.rebuild_model())
        ttk.Label(view, text="X =").pack(side="left", padx=(6, 0))
        self.min_block_var = tk.StringVar(value="16")
        mb = ttk.Entry(view, textvariable=self.min_block_var, width=8, font=theme.mono_font())
        mb.pack(side="left", padx=3)
        mb.bind("<Return>", lambda e: self.rebuild_model())
        ttk.Label(view, text="Region").pack(side="left", padx=(10, 0))
        self.region_box = ttk.Combobox(view, state="readonly", width=22)
        self.region_box.pack(side="left", padx=3)
        self.region_box.bind("<<ComboboxSelected>>", lambda e: self.rebuild_model())
        ttk.Label(view, text="Layout").pack(side="left", padx=(10, 0))
        self.row_box = ttk.Combobox(view, values=list(ROW_SIZES), state="readonly", width=18)
        self.row_box.set("16 bytes/row")
        self.row_box.pack(side="left", padx=3)
        self.row_box.bind("<<ComboboxSelected>>", lambda e: self.rebuild_model())
        self.view_info = ttk.Label(view, style="Muted.TLabel")
        self.view_info.pack(side="right")

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=6)
        self.text = tk.Text(body, wrap="none", font=theme.mono_font(), background="#ffffff",
                            foreground=theme.FG, borderwidth=1, relief="solid", highlightthickness=0,
                            cursor="arrow", insertwidth=0, padx=6, pady=4, takefocus=1)
        self.vbar = ttk.Scrollbar(body, orient="vertical", command=self._on_vscroll)
        hbar = ttk.Scrollbar(body, orient="horizontal", command=self.text.xview)
        self.text.configure(xscrollcommand=hbar.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        self.vbar.grid(row=0, column=1, sticky="ns")
        hbar.grid(row=1, column=0, sticky="ew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        t = self.text
        t.tag_configure("currow", background=theme.HX_CURROW_BG)
        t.tag_configure("hdr", foreground=theme.MUTED, font=theme.mono_bold_font())
        t.tag_configure("addr", foreground=theme.ACCENT)
        t.tag_configure("off", foreground=theme.MUTED)
        t.tag_configure("nodata", foreground="#a7b0b8")
        t.tag_configure("same", foreground=theme.FG)
        t.tag_configure("diff", background=theme.HX_DIFF_BG, foreground=theme.HX_DIFF_FG)
        t.tag_configure("exp", background=theme.HX_EXP_BG, foreground=theme.HX_EXP_FG)
        t.tag_configure("onlya", background=theme.HX_ONLYA_BG)
        t.tag_configure("onlyb", background=theme.HX_ONLYB_BG)
        t.tag_configure("st_diff", foreground=theme.C_CHANGED, font=theme.mono_bold_font())
        t.tag_configure("st_exp", foreground=theme.C_EXPECTED, font=theme.mono_bold_font())
        t.tag_configure("st_same", foreground=theme.C_SAME)
        t.tag_configure("match", underline=True, background="#bfe3ff")
        t.tag_configure("cursor", background=theme.HX_CURSOR_BG, foreground="#ffffff")
        t.tag_raise("cursor")

        t.bind("<Configure>", lambda e: self._on_resize())
        t.bind("<MouseWheel>", self._on_wheel)
        t.bind("<Button-4>", lambda e: self.scroll_rows(-3))
        t.bind("<Button-5>", lambda e: self.scroll_rows(3))
        t.bind("<Button-1>", self._on_click)
        for key, fn in (("<Up>", lambda e: self.move_cursor_rows(-1)), ("<Down>", lambda e: self.move_cursor_rows(1)),
                        ("<Left>", lambda e: self.move_cursor_bytes(-1)), ("<Right>", lambda e: self.move_cursor_bytes(1)),
                        ("<Prior>", lambda e: self.move_cursor_rows(-self.visible_lines)),
                        ("<Next>", lambda e: self.move_cursor_rows(self.visible_lines)),
                        ("<Control-Home>", lambda e: self.move_cursor_rows(-10 ** 12)),
                        ("<Control-End>", lambda e: self.move_cursor_rows(10 ** 12)),
                        ("n", lambda e: self.next_diff()), ("p", lambda e: self.prev_diff())):
            t.bind(key, lambda e, f=fn: (f(e), "break")[1])
        self.bind("<F3>", lambda e: self.search(False))
        self.bind("<Shift-F3>", lambda e: self.search(True))
        self.bind("<Control-g>", lambda e: (self._goto_entry.focus_set(), "break")[1])

        legend = ttk.Frame(self, padding=(6, 2))
        legend.pack(fill="x")
        for text, bg in (("unexpected difference", theme.HX_DIFF_BG), ("expected difference", theme.HX_EXP_BG),
                         ("only in A", theme.HX_ONLYA_BG), ("only in B", theme.HX_ONLYB_BG)):
            tk.Label(legend, text="  ", background=bg, relief="solid", borderwidth=1).pack(side="left", padx=(0, 3))
            ttk.Label(legend, text=text, style="Muted.TLabel").pack(side="left", padx=(0, 12))
        ttk.Label(legend, text="Keys: arrows/PgUp/PgDn move · n/p next/prev diff · F3 find · Ctrl+G go to · "
                               "'+0x..' = file offset, addresses are MCU addresses",
                  style="Muted.TLabel").pack(side="right")
        self.status = ttk.Label(self, padding=(6, 3), font=theme.mono_font(), anchor="w",
                                background="#dde2e7")
        self.status.pack(fill="x")

    # ------------------------------------------------------------- session
    def set_session(self, session, start_addr: int | None = None) -> None:
        self.session = session
        if session is None:
            self.model = None
            self.text.configure(state="normal")
            self.text.delete("1.0", "end")
            self.text.insert("end", "No comparison loaded.")
            self.text.configure(state="disabled")
            return
        self.aw = addr_width(session.diff.union.last)
        names = [ALL_REGIONS] + [r.name for r in session.config.regions]
        self.region_box.configure(values=names)
        if self.region_box.get() not in names:
            self.region_box.set(ALL_REGIONS)
        self._block_starts = [b.start for b in session.analysis.blocks]
        if start_addr is None:
            start_addr = self.cursor if self.cursor is not None else (
                session.diff.first_diff if session.diff.first_diff is not None else session.diff.union.first)
        self.rebuild_model(keep=start_addr)

    def rebuild_model(self, keep: int | None = None) -> None:
        if self.session is None:
            return
        an, diff = self.session.analysis, self.session.diff
        region = None
        rname = self.region_box.get()
        if rname and rname != ALL_REGIONS:
            region = next((r for r in self.session.config.regions if r.name == rname), None)
        mode = self.filter_box.get()
        min_block = 0
        if mode == FILTER_BLOCKS:
            try:
                min_block = parse_int(self.min_block_var.get())
            except ValueError as exc:
                self.set_status(f"Invalid block size: {exc}", error=True)
                return
        intervals = build_view_intervals(diff, an, mode, region, min_block)
        self.bpr = ROW_SIZES.get(self.row_box.get(), 16)
        self.model = HexRowModel(intervals, self.bpr)
        self._compute_layout()
        target = keep if keep is not None else self.cursor
        if target is None:
            target = intervals.first
        self.view_info.configure(text=f"{intervals.total:,} bytes in view · {self.model.total_rows:,} rows")
        if target is not None and self.model.total_rows:
            self.set_cursor(target, center=True)
        else:
            self.top_row = 0
            self.render()
            if not self.model.total_rows:
                self.set_status("Nothing to show for this filter.")

    # -------------------------------------------------------------- layout
    def _compute_layout(self) -> None:
        aw = self.aw + 2
        ow = 10
        if self.bpr == 1:
            cols = f"{'Address':<{aw}}  {'Offset A':>{ow}}  {'Offset B':>{ow}}   A    B    Status          Region"
            self._layout = {"a": aw + 2 + ow + 2 + ow + 3, "b": aw + 2 + ow + 2 + ow + 3 + 5, "cell": 2}
        else:
            n = self.bpr
            hexw = n * 3
            a_start = aw + 2 + ow + 1 + ow + 3
            b_start = a_start + hexw + 2
            asc_a = b_start + hexw + 2
            asc_b = asc_a + n + 3
            idx = " ".join(f"{i:02X}" for i in range(n))
            cols = (f"{'Address':<{aw}}  {'Offset A':>{ow}} {'Offset B':>{ow}} | {idx[:hexw - 1]:<{hexw - 1}} "
                    f"| {idx[:hexw - 1]:<{hexw - 1}} | {'ASCII A':<{n}} | {'ASCII B':<{n}} | Diff")
            self._layout = {"a": a_start, "b": b_start, "cell": 3, "asc_a": asc_a, "asc_b": asc_b}
        self._header = cols
        self._header2 = (" " * self._layout["a"] + "Firmware A".ljust(self._layout["b"] - self._layout["a"])
                         + "Firmware B") if self.bpr > 1 else ""

    # ------------------------------------------------------------- rendering
    def _on_resize(self) -> None:
        line_h = theme.mono_font().metrics("linespace") + 1
        lines = max(1, (self.text.winfo_height() - 8) // line_h - (3 if self.bpr > 1 else 2))
        if lines != self.visible_lines:
            self.visible_lines = lines
            self.render()

    def render(self) -> None:
        t = self.text
        t.configure(state="normal")
        t.delete("1.0", "end")
        if self.model is None or self.session is None:
            t.configure(state="disabled")
            return
        total = self.model.total_rows
        self.top_row = max(0, min(self.top_row, max(0, total - self.visible_lines)))
        if self._header2:
            t.insert("end", self._header2 + "\n", "hdr")
        t.insert("end", self._header + "\n", "hdr")
        for i in range(self.visible_lines):
            row = self.top_row + i
            if row >= total:
                break
            addr = self.model.row_to_addr(row)
            pieces = self._row_pieces(addr) if self.bpr > 1 else self._byte_pieces(addr)
            t.insert("end", *pieces)
            t.insert("end", "\n")
        t.configure(state="disabled")
        if total:
            self.vbar.set(self.top_row / total, min(1.0, (self.top_row + self.visible_lines) / total))
        else:
            self.vbar.set(0, 1)

    def _classify(self, addr: int, a, b) -> tuple[str, str]:
        """(cell tag, status text) for one byte."""
        diff, an = self.session.diff, self.session.analysis
        if a is None and b is None:
            return "nodata", NO_DATA
        st = diff.status_at(addr)
        if st in (SAME, SAME_FILL):
            return "same", st
        exp = an.expected_set.contains(addr)
        if exp:
            return "exp", f"{st} (expected)"
        return {DIFF: "diff", ONLY_A: "onlya", ONLY_B: "onlyb"}.get(st, "diff"), st

    def _extra(self, addr: int, tag: str) -> tuple:
        tags = [tag]
        if self.match and self.match[0] <= addr < self.match[0] + self.match[1]:
            tags.append("match")
        if addr == self.cursor:
            tags.append("cursor")
        return tuple(tags)

    def _offsets(self, addr: int) -> tuple[str, str]:
        a, b = self.session.image_a, self.session.image_b
        return fmt_offset(a.file_offset_of(addr)), fmt_offset(b.file_offset_of(addr))

    def _byte_pieces(self, addr: int) -> list:
        a = self.session.image_a.byte_at(addr)
        b = self.session.image_b.byte_at(addr)
        tag, status = self._classify(addr, a, b)
        offa, offb = self._offsets(addr)
        row_tag = ("currow",) if addr == self.cursor else ()
        regions = ", ".join(self.session.analysis.regions_at(addr))
        st_tag = {"diff": "st_diff", "onlya": "st_diff", "onlyb": "st_diff", "exp": "st_exp"}.get(tag, "st_same")
        return [fmt_addr(addr, self.aw), ("addr",) + row_tag,
                f"  {offa:>10}  {offb:>10}   ", ("off",) + row_tag,
                "--" if a is None else f"{a:02X}", self._extra(addr, tag), "   ", row_tag,
                "--" if b is None else f"{b:02X}", self._extra(addr, tag), "   ", row_tag,
                f"{status:<16}", (st_tag,) + row_tag, regions, row_tag]

    def _row_pieces(self, addr: int) -> list:
        n = self.bpr
        img_a, img_b = self.session.image_a, self.session.image_b
        va, vb = img_a.read(addr, n), img_b.read(addr, n)
        offa, offb = self._offsets(addr) if (va[0] is not None or vb[0] is not None) else ("", "")
        cur_row = self.cursor is not None and addr <= self.cursor < addr + n
        rt = ("currow",) if cur_row else ()
        out = [fmt_addr(addr, self.aw), ("addr",) + rt, f"  {offa:>10} {offb:>10} | ", ("off",) + rt]
        tags = []
        ndiff = 0
        visible = self.model.contains_addr
        for i in range(n):
            tag, _ = self._classify(addr + i, va[i], vb[i])
            if tag in ("diff", "exp", "onlya", "onlyb"):
                ndiff += 1
            if not visible(addr + i) and tag != "nodata":
                tag = "nodata" if tag == "same" else tag  # neighbour bytes outside the filter are dimmed
            tags.append(tag)
        for vals in (va, vb):
            for i, v in enumerate(vals):
                out += ["--" if v is None else f"{v:02X}", self._extra(addr + i, tags[i]) + rt, " ", rt]
            out += ["| ", rt]
        for vals in (va, vb):
            asc = "".join("·" if v is None else (chr(v) if 32 <= v < 127 else ".") for v in vals)
            out += [asc, ("off",) + rt, " | ", rt]
        out += [f"{ndiff:>3}" if ndiff else "  ·", (("st_diff" if ndiff else "st_same"),) + rt]
        return out

    # ------------------------------------------------------------ scrolling
    def _on_vscroll(self, *args) -> None:
        if self.model is None:
            return
        total = self.model.total_rows
        if args[0] == "moveto":
            self.top_row = int(float(args[1]) * total)
        elif args[0] == "scroll":
            step = int(args[1]) * (self.visible_lines if args[2] == "pages" else 1)
            self.top_row += step
        self.render()

    def _on_wheel(self, event) -> str:
        self.scroll_rows(-3 * (1 if event.delta > 0 else -1) * max(1, abs(event.delta) // 120))
        return "break"

    def scroll_rows(self, n: int) -> None:
        self.top_row += n
        self.render()

    # --------------------------------------------------------------- cursor
    def set_cursor(self, addr: int, center: bool = False, quiet: bool = False) -> None:
        if self.model is None or not self.model.total_rows:
            return
        row = self.model.addr_to_row(addr)
        row_addr = self.model.row_to_addr(row)
        if not (row_addr <= addr < row_addr + self.bpr):
            addr = row_addr  # snapped to the nearest row of the current view
            if not quiet:
                self.set_status("Requested address is not part of the current view; moved to the nearest row.",
                                error=True)
                quiet = True
        self.cursor = addr
        if center or not (self.top_row <= row < self.top_row + self.visible_lines):
            self.top_row = max(0, row - self.visible_lines // 3)
        self.render()
        if not quiet:
            self._update_status()

    def move_cursor_rows(self, n: int) -> None:
        if self.cursor is None or self.model is None or not self.model.total_rows:
            return
        row = self.model.addr_to_row(self.cursor)
        col = self.cursor % self.bpr
        new_row = max(0, min(self.model.total_rows - 1, row + n))
        self.set_cursor(self.model.row_to_addr(new_row) + col)

    def move_cursor_bytes(self, n: int) -> None:
        if self.cursor is None:
            return
        target = self.cursor + n
        row = self.model.addr_to_row(target)
        ra = self.model.row_to_addr(row)
        if not (ra <= target < ra + self.bpr):
            # stepped into a hidden gap: jump to the adjacent row
            row = self.model.addr_to_row(self.cursor) + (1 if n > 0 else -1)
            row = max(0, min(self.model.total_rows - 1, row))
            ra = self.model.row_to_addr(row)
            target = ra if n > 0 else ra + self.bpr - 1
        self.set_cursor(target)

    def _on_click(self, event) -> str:
        self.text.focus_set()
        index = self.text.index(f"@{event.x},{event.y}")
        line, col = (int(x) for x in index.split("."))
        line -= 2 if self.bpr > 1 else 1
        if line < 1 or self.model is None:
            return "break"
        row = self.top_row + line - 1
        addr = self.model.row_to_addr(row)
        if addr is None:
            return "break"
        if self.bpr > 1:
            lay, n = self._layout, self.bpr
            for start in (lay["a"], lay["b"]):
                if start <= col < start + n * 3:
                    addr += min(n - 1, (col - start) // 3)
                    break
            else:
                for start in (lay["asc_a"], lay["asc_b"]):
                    if start <= col < start + n:
                        addr += col - start
                        break
        self.set_cursor(addr)
        return "break"

    # ----------------------------------------------------------- navigation
    def _jump(self, addr: int | None, what: str) -> None:
        if addr is None:
            self.set_status(f"No {what}.", error=True)
            return
        self.set_cursor(addr, center=True)

    def first_diff(self) -> None:
        self._jump(self.session.diff.diff.first if self.session else None, "differences")

    def last_diff(self) -> None:
        d = self.session.diff.diff if self.session else None
        self._jump(d.starts[-1] if d else None, "differences")

    def next_diff(self) -> None:
        if not self.session:
            return
        cur = self.cursor if self.cursor is not None else -1
        self._jump(self.session.diff.diff.next_start_after(cur), "further difference after the cursor")

    def prev_diff(self) -> None:
        if not self.session:
            return
        cur = self.cursor if self.cursor is not None else 0
        self._jump(self.session.diff.diff.prev_start_before(cur), "difference before the cursor")

    def goto(self) -> None:
        if not self.session:
            return
        text = self.goto_var.get().strip()
        mode = self.goto_mode.get()
        if text.startswith("+"):
            mode, text = "File offset A", text[1:]
        try:
            value = parse_int(text)
        except ValueError as exc:
            self.set_status(f"Go to: {exc}", error=True)
            return
        if mode == "Address":
            addr = value
        else:
            img = self.session.image_a if mode.endswith("A") else self.session.image_b
            addr = img.address_of_file_offset(value)
            if addr is None:
                self.set_status(f"File offset +0x{value:X} is not loaded data of {img.name}.", error=True)
                return
        if not self.session.diff.union.contains(addr):
            nxt = self.session.diff.union.first_at_or_after(addr)
            self.set_status(f"No firmware data at {fmt_addr(addr, self.aw)}"
                            + (f"; showing next data at {fmt_addr(nxt, self.aw)}." if nxt is not None else "."),
                            error=True)
            if nxt is None:
                return
            self.set_cursor(nxt, center=True, quiet=True)
            return
        self.set_cursor(addr, center=True)

    def search(self, backwards: bool) -> None:
        if not self.session:
            return
        try:
            pattern = parse_hex_bytes(self.search_var.get())
        except ValueError as exc:
            self.set_status(f"Search: {exc}", error=True)
            return
        side = self.search_side.get()
        imgs = {"A": [self.session.image_a], "B": [self.session.image_b],
                "A or B": [self.session.image_a, self.session.image_b]}[side]
        if self.cursor is None:
            start = 0 if not backwards else (1 << 64) - 1
        else:
            start = self.cursor if backwards else self.cursor + 1
        hits = [h for h in (img.find(pattern, start, backwards) for img in imgs) if h is not None]
        if not hits:
            self.set_status(f"Pattern {pattern.hex(' ').upper()} not found {'before' if backwards else 'after'} "
                            f"the cursor in {side}.", error=True)
            return
        hit = max(hits) if backwards else min(hits)
        self.match = (hit, len(pattern))
        self.set_cursor(hit, center=True)

    # --------------------------------------------------------------- status
    def set_status(self, text: str, error: bool = False) -> None:
        self.status.configure(text=text, foreground=theme.C_CHANGED if error else theme.FG)

    def _update_status(self) -> None:
        if self.cursor is None or self.session is None:
            return
        addr = self.cursor
        a = self.session.image_a.byte_at(addr)
        b = self.session.image_b.byte_at(addr)
        _, status = self._classify(addr, a, b)
        offa, offb = self._offsets(addr)
        regions = ", ".join(self.session.analysis.regions_at(addr)) or "-"
        blk = ""
        blocks = self.session.analysis.blocks
        i = bisect_right(self._block_starts, addr) - 1
        if i >= 0 and addr < blocks[i].end:
            blk = f" | Block #{blocks[i].index} ({blocks[i].size:,} B, {blocks[i].classification})"
        sym = self.session.symbol_label(addr)
        if sym:
            blk += f" | {sym}"
        av = "--" if a is None else f"0x{a:02X}"
        bv = "--" if b is None else f"0x{b:02X}"
        self.set_status(f"Address {fmt_addr(addr, self.aw)} | A {av} @file {offa} | B {bv} @file {offb} | "
                        f"{status} | Region: {regions}{blk}")
