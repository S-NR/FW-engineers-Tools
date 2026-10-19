"""Memory-map visualisation: region tree, linear region/change bars and a block grid."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..core.analysis import ST_CHANGED, ST_EXPECTED, ST_NOT_PRESENT, ST_SAME
from ..core.formatting import addr_width, fmt_addr, fmt_size
from ..core.memory_map_model import build_cells, gap_positions, rank
from . import theme

REGION_FILL = {ST_SAME: "#c9e5cf", ST_CHANGED: "#e88a8a", ST_EXPECTED: "#f2c14e", ST_NOT_PRESENT: "#e3e6ea"}
CELL = 10
MARGIN = 10
BAR_H = 26


class MemoryMapView(ttk.Frame):
    def __init__(self, master, on_open_address):
        super().__init__(master, padding=(14, 12))
        self.on_open_address = on_open_address
        self.session = None
        self._cells = []
        self._strip = []
        self._geom = {}
        self._pending = None

        left = ttk.Frame(self)
        left.pack(side="left", fill="y")
        ttk.Label(left, text="Region layout", style="TLabelframe.Label").pack(anchor="w")
        self.tree_text = tk.Text(left, width=58, wrap="none", font=theme.mono_font(), background="#ffffff",
                                 relief="flat", borderwidth=0, highlightthickness=1, highlightbackground=theme.BORDER, padx=10, pady=8)
        self.tree_text.pack(fill="y", expand=True, pady=(2, 0))
        for status, color in theme.STATUS_COLORS.items():
            self.tree_text.tag_configure(status, foreground=color, font=theme.bold_font())
        self.tree_text.tag_configure("addr", foreground=theme.ACCENT)
        self.tree_text.tag_configure("muted", foreground=theme.MUTED)

        right = ttk.Frame(self)
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))
        ttk.Label(right, text="Byte / block map (click to open in hex viewer)",
                  style="TLabelframe.Label").pack(anchor="w")
        self.canvas = tk.Canvas(right, background="#ffffff", highlightthickness=1,
                                highlightbackground=theme.BORDER, cursor="hand2")
        self.canvas.pack(fill="both", expand=True, pady=(2, 0))
        self.info = ttk.Label(right, font=theme.mono_font(), anchor="w")
        self.info.pack(fill="x", pady=(4, 0))
        legend = ttk.Frame(right)
        legend.pack(fill="x")
        for text, key in (("unchanged", "same"), ("expected change only", "expected"),
                          ("unexpected change", "changed"), ("no data", "nodata")):
            tk.Label(legend, text="  ", background=theme.MAP_COLORS[key], relief="solid",
                     borderwidth=1).pack(side="left", padx=(0, 3))
            ttk.Label(legend, text=text, style="Muted.TLabel").pack(side="left", padx=(0, 12))
        self.scale_label = ttk.Label(legend, style="Muted.TLabel")
        self.scale_label.pack(side="right")

        self.canvas.bind("<Configure>", lambda e: self._schedule_redraw())
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Button-1>", self._on_click)

    def set_session(self, session) -> None:
        self.session = session
        self._render_tree()
        self._schedule_redraw()

    # ------------------------------------------------------------ region tree
    def _render_tree(self) -> None:
        t = self.tree_text
        t.configure(state="normal")
        t.delete("1.0", "end")
        if self.session is None:
            t.insert("end", "No comparison loaded.", "muted")
            t.configure(state="disabled")
            return
        an = self.session.analysis
        w = addr_width(self.session.diff.union.last)
        results = sorted(an.region_results, key=lambda r: r.region.start)
        if not results:
            t.insert("end", "No memory regions configured.\n\nAdd regions on the Configuration tab.", "muted")
        else:
            t.insert("end", fmt_addr(results[0].region.start, w) + "\n", "addr")
            t.insert("end", "│\n", "muted")
            name_w = max(len(r.region.name) for r in results)
            for i, r in enumerate(results):
                branch = "└── " if i == len(results) - 1 else "├── "
                t.insert("end", branch, "muted")
                t.insert("end", f"{r.region.name:<{name_w}}  ")
                t.insert("end", f"[ {r.status} ]", r.status)
                t.insert("end", f"\n{'    ' if i == len(results) - 1 else '│   '}", "muted")
                t.insert("end", f"{fmt_addr(r.region.start, w)}-{fmt_addr(r.region.end, w)}  "
                                f"{fmt_size(r.size)}, {r.changed_bytes:,} B changed\n", "muted")
            if an.outside_changed:
                t.insert("end", f"\n(outside regions) {an.outside_changed:,} B changed ", "muted")
                t.insert("end", "[ CHANGED ]", "CHANGED")
                t.insert("end", "\n")
        if an.config.expected:
            t.insert("end", "\nExpected-difference areas\n", "muted")
            for er in an.expected_results:
                t.insert("end", f"  {er.area.name:<18} {fmt_addr(er.area.start, w)}  ")
                t.insert("end", er.status + "\n", "EXPECTED" if er.changed_bytes else "SAME")
        t.configure(state="disabled")

    # --------------------------------------------------------------- drawing
    def _schedule_redraw(self) -> None:
        if self._pending is not None:
            self.after_cancel(self._pending)
        self._pending = self.after(60, self._redraw)

    def _redraw(self) -> None:
        self._pending = None
        c = self.canvas
        c.delete("all")
        self._cells, self._strip = [], []
        if self.session is None:
            return
        diff, an = self.session.diff, self.session.analysis
        union = diff.union
        width, height = c.winfo_width(), c.winfo_height()
        if union.total == 0 or width < 60 or height < 80:
            c.create_text(width // 2, height // 2, text="No firmware data", fill=theme.MUTED)
            return
        inner_w = width - 2 * MARGIN
        total = union.total

        # 1. region band (linear compressed axis)
        y0 = MARGIN
        c.create_rectangle(MARGIN, y0, MARGIN + inner_w, y0 + BAR_H, fill=theme.MAP_COLORS["nodata"], outline="")
        for r in an.region_results:
            r0, r1 = rank(union, r.region.start), rank(union, r.region.stop)
            if r1 <= r0:
                continue
            x0 = MARGIN + r0 * inner_w / total
            x1 = MARGIN + r1 * inner_w / total
            c.create_rectangle(x0, y0, x1, y0 + BAR_H, fill=REGION_FILL.get(r.status, "#dddddd"),
                               outline="#ffffff")
            label = f"{r.region.name} [{r.status}]"
            if x1 - x0 > theme.mono_font().measure(label) + 6:
                c.create_text((x0 + x1) / 2, y0 + BAR_H / 2, text=label, font=theme.bold_font(), fill=theme.FG)
            elif x1 - x0 > theme.mono_font().measure(r.region.name) + 6:
                c.create_text((x0 + x1) / 2, y0 + BAR_H / 2, text=r.region.name, fill=theme.FG)
        for pos, gs, ge in gap_positions(union):
            x = MARGIN + pos * inner_w / total
            c.create_line(x, y0 - 4, x, y0 + BAR_H + 4, fill="#333333", dash=(2, 2))

        # 2. linear change strip, one cell per pixel column
        y1 = y0 + BAR_H + 6
        strip, per_px = build_cells(union, diff.diff, an.expected_diff, inner_w)
        self._strip = strip
        px_w = inner_w / max(1, len(strip))
        for cell in strip:
            if cell.status in ("changed", "expected"):
                x = MARGIN + cell.index * px_w
                c.create_rectangle(x, y1, x + max(1.0, px_w), y1 + 14, fill=theme.MAP_COLORS[cell.status], outline="")
        c.create_rectangle(MARGIN, y1, MARGIN + inner_w, y1 + 14, outline=theme.BORDER)

        # 3. block grid
        y2 = y1 + 24
        cols = max(1, inner_w // CELL)
        rows = max(1, (height - y2 - MARGIN) // CELL)
        cells, per = build_cells(union, diff.diff, an.expected_diff, cols * rows)
        self._cells = cells
        for cell in cells:
            x = MARGIN + (cell.index % cols) * CELL
            y = y2 + (cell.index // cols) * CELL
            c.create_rectangle(x, y, x + CELL - 1, y + CELL - 1, fill=theme.MAP_COLORS[cell.status], outline="")
        self._geom = {"y1": y1, "y2": y2, "cols": cols, "px_w": px_w, "w": addr_width(union.last)}
        gaps = len(union) - 1
        self.scale_label.configure(
            text=f"grid cell = {per:,} B · strip px = {per_px:,} B" + (f" · {gaps} gap(s) omitted (dashed)" if gaps else ""))

    # ----------------------------------------------------------- interaction
    def _cell_at(self, x: int, y: int):
        g = self._geom
        if not g:
            return None
        if g["y1"] <= y <= g["y1"] + 14 and self._strip:
            i = int((x - MARGIN) / g["px_w"])
            return self._strip[i] if 0 <= i < len(self._strip) else None
        if y >= g["y2"] and self._cells:
            col = (x - MARGIN) // CELL
            row = (y - g["y2"]) // CELL
            if 0 <= col < g["cols"]:
                i = row * g["cols"] + col
                return self._cells[i] if 0 <= i < len(self._cells) else None
        return None

    def _on_motion(self, event) -> None:
        cell = self._cell_at(event.x, event.y)
        if cell is None:
            self.info.configure(text="")
            return
        w = self._geom["w"]
        self.info.configure(text=f"{fmt_addr(cell.start, w)} - {fmt_addr(cell.end - 1, w)}   "
                                 f"{cell.changed:,} of {cell.data_bytes:,} B changed "
                                 f"({cell.expected:,} expected)   {cell.status.upper()}")

    def _on_click(self, event) -> None:
        cell = self._cell_at(event.x, event.y)
        if cell is None or self.session is None:
            return
        target = self.session.diff.diff.first_at_or_after(cell.start)
        if target is None or target >= cell.end:
            target = cell.start
        self.on_open_address(target)
