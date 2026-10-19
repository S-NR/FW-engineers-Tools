"""Metadata, checksum and ELF tabs."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..core import checksum as checksum_mod
from ..core.formatting import addr_width, fmt_addr, fmt_int, parse_int
from ..core.metadata_detector import DISCLAIMER
from ..core.region_manager import parse_range_text
from . import theme
from .widgets import DataTable, TaskRunner


class MetadataView(ttk.Frame):
    def __init__(self, master, on_open_address):
        super().__init__(master, padding=(14, 12))
        self.on_open_address = on_open_address
        self.session = None
        ttk.Label(self, text="⚠ " + DISCLAIMER, style="Warn.TLabel", wraplength=1100,
                  justify="left").pack(fill="x", pady=(0, 6))
        self.summary = DataTable(self, [("item", "Item", 140, "w"), ("a", "Firmware A", 300, "w"),
                                        ("b", "Firmware B", 300, "w"), ("status", "Status", 110, "w")], height=6)
        self.summary.pack(fill="x")
        ttk.Label(self, text="All detected candidates (double-click to view in hex viewer)",
                  style="TLabelframe.Label").pack(anchor="w", pady=(8, 2))
        self.details = DataTable(self, [("side", "Side", 45, "center"), ("cat", "Item", 130, "w"),
                                        ("value", "Value", 220, "w"), ("addr", "Address", 110, "w"),
                                        ("conf", "Confidence", 85, "w"), ("ctx", "Found in string", 420, "w")],
                                 height=8)
        self.details.pack(fill="both", expand=True)
        self.details.tree.bind("<Double-1>", self._open)
        self._items = {}

    def show(self, session) -> None:
        self.session = session
        if session is None:
            self.summary.clear()
            self.details.clear()
            return
        self.summary.set_rows([(m.category, (m.category, ", ".join(m.values_a) or "-", ", ".join(m.values_b) or "-",
                                             m.status), (m.status,), (m.category, m.values_a, m.values_b, m.status))
                               for m in session.metadata_diff])
        w = addr_width(session.diff.union.last)
        rows = []
        self._items = {}
        for side, items in (("A", session.metadata_a), ("B", session.metadata_b)):
            for i, it in enumerate(items):
                iid = f"{side}{i}"
                self._items[iid] = it.address
                rows.append((iid, (side, it.category, it.value, fmt_addr(it.address, w), it.confidence, it.context),
                             (), (side, it.category, it.value, it.address, it.confidence, it.context)))
        self.details.set_rows(rows)

    def _open(self, _e=None) -> None:
        iid = self.details.selected_iid()
        if iid in self._items:
            self.on_open_address(self._items[iid])


SCOPE_FILE = "Entire file (as stored on disk)"
SCOPE_IMAGE = "Image data (address order, gaps skipped)"
SCOPE_RANGE = "Address range (gaps filled)"


class ChecksumView(ttk.Frame):
    def __init__(self, master, get_images, get_regions):
        super().__init__(master, padding=(14, 12))
        self.get_images = get_images
        self.get_regions = get_regions
        self.runner = TaskRunner(self)
        form = ttk.Frame(self)
        form.pack(fill="x")
        ttk.Label(form, text="Algorithm").grid(row=0, column=0, sticky="w")
        self.alg = ttk.Combobox(form, values=checksum_mod.algorithm_names(), state="readonly", width=22)
        self.alg.set("CRC-32")
        self.alg.grid(row=0, column=1, sticky="w", padx=4, pady=2)
        self.alg.bind("<<ComboboxSelected>>", lambda e: self._describe())
        self.alg_desc = ttk.Label(form, style="Muted.TLabel")
        self.alg_desc.grid(row=0, column=2, columnspan=4, sticky="w", padx=8)
        ttk.Label(form, text="Scope").grid(row=1, column=0, sticky="w")
        self.scope = ttk.Combobox(form, state="readonly", width=44)
        self.scope.grid(row=1, column=1, columnspan=2, sticky="w", padx=4, pady=2)
        self.scope.bind("<<ComboboxSelected>>", lambda e: self._scope_changed())
        self.scope.bind("<Button-1>", lambda e: self.refresh_scopes())
        ttk.Label(form, text="Start").grid(row=2, column=0, sticky="w")
        self.start_var, self.end_var, self.fill_var = tk.StringVar(), tk.StringVar(), tk.StringVar(value="0xFF")
        ttk.Entry(form, textvariable=self.start_var, width=14, font=theme.mono_font()).grid(row=2, column=1, sticky="w", padx=4)
        ttk.Label(form, text="End (incl.) or +size").grid(row=2, column=2, sticky="e")
        ttk.Entry(form, textvariable=self.end_var, width=14, font=theme.mono_font()).grid(row=2, column=3, sticky="w", padx=4)
        ttk.Label(form, text="Gap fill byte").grid(row=2, column=4, sticky="e", padx=(12, 0))
        ttk.Entry(form, textvariable=self.fill_var, width=6, font=theme.mono_font()).grid(row=2, column=5, sticky="w", padx=4)
        ttk.Button(form, text="Calculate", style="Accent.TButton", command=self.calculate).grid(
            row=3, column=1, sticky="w", padx=4, pady=8)
        res = ttk.LabelFrame(self, text="Result", padding=8)
        res.pack(fill="x", pady=6)
        self.out = {}
        for i, key in enumerate(("Firmware A", "Firmware B", "Match")):
            ttk.Label(res, text=key + ":", style="Key.TLabel").grid(row=i, column=0, sticky="w")
            var = tk.StringVar(value="-")
            ttk.Entry(res, textvariable=var, state="readonly", width=70, font=theme.mono_font()).grid(
                row=i, column=1, sticky="we", padx=6, pady=1)
            self.out[key] = var
        ttk.Label(self, style="Muted.TLabel", wraplength=1000, justify="left", text=(
            "Checksums are calculated by this tool for information only. They do not verify the firmware's "
            "internal CRC: the firmware may use a different polynomial, initial value, byte order or address "
            "range. Select the algorithm and range your firmware actually uses to cross-check an embedded CRC."
        )).pack(fill="x", pady=(4, 0))
        self.refresh_scopes()
        self._describe()
        self._scope_changed()

    def _describe(self) -> None:
        self.alg_desc.configure(text=checksum_mod.describe(self.alg.get()))

    def refresh_scopes(self) -> None:
        values = [SCOPE_FILE, SCOPE_IMAGE, SCOPE_RANGE] + [f"Region: {r.name}" for r in self.get_regions()]
        self.scope.configure(values=values)
        if self.scope.get() not in values:
            self.scope.set(SCOPE_IMAGE)

    def _scope_changed(self) -> None:
        scope = self.scope.get()
        if scope.startswith("Region: "):
            name = scope[len("Region: "):]
            r = next((x for x in self.get_regions() if x.name == name), None)
            if r:
                self.start_var.set(f"0x{r.start:08X}")
                self.end_var.set(f"0x{r.end:08X}")

    def calculate(self) -> None:
        img_a, img_b = self.get_images()
        if img_a is None and img_b is None:
            self.out["Match"].set("Load at least one firmware file first.")
            return
        alg, scope = self.alg.get(), self.scope.get()
        try:
            if scope in (SCOPE_FILE, SCOPE_IMAGE):
                start = end = fill = None
            else:
                s, e = parse_range_text(self.start_var.get(), self.end_var.get())
                start, end = s, e + 1
                fill = parse_int(self.fill_var.get())
                if fill > 0xFF:
                    raise ValueError("fill byte must be 0x00-0xFF")
        except ValueError as exc:
            self.out["Match"].set(f"Error: {exc}")
            return
        for k in ("Firmware A", "Firmware B"):
            self.out[k].set("calculating…")
        self.out["Match"].set("")

        def calc(img):
            if img is None:
                return "(not loaded)"
            if scope == SCOPE_FILE:
                return checksum_mod.checksum_file(alg, img.path)
            return checksum_mod.checksum_image(alg, img, start, end, fill)

        if self.runner.busy:
            return
        self.runner.run(lambda: (calc(img_a), calc(img_b)),
                        lambda res: self._done(res[0], res[1], None, scope),
                        lambda exc, tb: self._done("-", "-", str(exc), scope))

    def _done(self, ra, rb, err, scope) -> None:
        self.out["Firmware A"].set(ra)
        self.out["Firmware B"].set(rb)
        if err:
            self.out["Match"].set(f"Error: {err}")
        elif "(not loaded)" in (ra, rb):
            self.out["Match"].set("-")
        else:
            self.out["Match"].set(("SAME" if ra == rb else "DIFFERENT") + f"   ({scope})")


class ElfView(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=(14, 12))
        self.msg = ttk.Label(self, style="Muted.TLabel", wraplength=1000, justify="left")
        self.msg.pack(fill="x")
        self.info = DataTable(self, [("item", "Item", 160, "w"), ("a", "Firmware A", 380, "w"),
                                     ("b", "Firmware B", 380, "w")], height=6)
        self.info.pack(fill="x", pady=(4, 6))
        ttk.Label(self, text="Sections", style="TLabelframe.Label").pack(anchor="w")
        self.sections = DataTable(self, [
            ("name", "Section", 140, "w"), ("type", "Type", 90, "w"), ("flags", "Flags", 50, "w"),
            ("addr_a", "Addr A", 105, "w"), ("addr_b", "Addr B", 105, "w"), ("size_a", "Size A", 90, "e"),
            ("size_b", "Size B", 90, "e"), ("diff", "Diff bytes", 90, "e"), ("status", "Status", 85, "w"),
            ("notes", "Notes", 320, "w")], height=8)
        self.sections.pack(fill="both", expand=True)
        self.sym_label = ttk.Label(self, text="Symbol changes", style="TLabelframe.Label")
        self.sym_label.pack(anchor="w", pady=(6, 0))
        self.symbols = DataTable(self, [
            ("name", "Symbol", 220, "w"), ("status", "Change", 120, "w"), ("type", "Type", 70, "w"),
            ("addr_a", "Addr A", 105, "w"), ("addr_b", "Addr B", 105, "w"), ("size_a", "Size A", 80, "e"),
            ("size_b", "Size B", 80, "e")], height=7, row_limit=10000)
        self.symbols.pack(fill="both", expand=True)

    def show(self, img_a, img_b, session) -> None:
        elf_a = img_a.elf if img_a is not None else None
        elf_b = img_b.elf if img_b is not None else None
        for t in (self.info, self.sections, self.symbols):
            t.clear()
        if elf_a is None and elf_b is None:
            self.msg.configure(text="ELF information is shown when an ELF file (.elf/.axf/.out) is loaded. "
                                    "Section and symbol comparison requires both files to be ELF.")
            return
        notes = []
        if elf_a is None or elf_b is None:
            notes.append("Only one file is ELF: its sections are listed, section comparison needs two ELF files.")
        if session is not None and session.elf_error:
            notes.append(session.elf_error)
        for img in (img_a, img_b):
            if img is not None and img.elf is not None and img.elf.warnings:
                notes += [f"{img.name}: {w}" for w in img.elf.warnings]
        self.msg.configure(text="\n".join(notes) or "Addresses: section VMA (sh_addr). Firmware bytes are compared "
                                                   "using load addresses (LMA) unless VMA mode is selected.")
        sa = elf_a.summary() if elf_a else {}
        sb = elf_b.summary() if elf_b else {}
        keys = list(dict.fromkeys(list(sa) + list(sb)))
        self.info.set_rows([(k, (k, sa.get(k, "-"), sb.get(k, "-")), (), (k, sa.get(k), sb.get(k))) for k in keys])
        w = 8
        if session is not None and session.elf is not None:
            rows = [(str(i), (s.name, s.type_name, s.flags, fmt_addr(s.addr_a, w), fmt_addr(s.addr_b, w),
                              fmt_int(s.size_a), fmt_int(s.size_b), fmt_int(s.diff_bytes), s.status, "; ".join(s.notes)),
                     (s.status,), (s.name, s.type_name, s.flags, s.addr_a, s.addr_b, s.size_a, s.size_b, s.diff_bytes,
                                   s.status, "")) for i, s in enumerate(session.elf.sections)]
            self.sections.set_rows(rows)
            syms = session.elf.symbols
            self.sym_label.configure(text=f"Symbol changes ({len(syms):,}; symbols A {session.elf.symbol_count_a:,} / "
                                          f"B {session.elf.symbol_count_b:,})")
            self.symbols.set_rows([(str(i), (s.name, s.status, s.type, fmt_addr(s.addr_a, w), fmt_addr(s.addr_b, w),
                                             fmt_int(s.size_a), fmt_int(s.size_b)), ("CHANGED",),
                                    (s.name, s.status, s.type, s.addr_a, s.addr_b, s.size_a, s.size_b))
                                   for i, s in enumerate(syms)])
        else:
            elf, side = (elf_a, "A") if elf_a else (elf_b, "B")
            rows = []
            for s in elf.sections:
                if s.type == 0:
                    continue
                vals = [s.name, s.type_name, s.flag_str, "-", "-", "-", "-", "-", "-", ""]
                if side == "A":
                    vals[3], vals[5] = fmt_addr(s.addr, w), fmt_int(s.size)
                else:
                    vals[4], vals[6] = fmt_addr(s.addr, w), fmt_int(s.size)
                rows.append((str(s.index), tuple(vals), (), (s.name, s.type_name, s.flag_str, s.addr, s.addr,
                                                               s.size, s.size, 0, "", "")))
            self.sections.set_rows(rows)
            self.sym_label.configure(text=f"Symbols: {len(elf.symbols):,} (comparison requires two ELF files)")
