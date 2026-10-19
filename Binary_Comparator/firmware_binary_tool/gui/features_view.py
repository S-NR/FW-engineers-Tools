"""'Changed Functions' tab: which functions, variables and source modules changed."""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, ttk

from ..core.feature_diff import REAL_CHANGES, SIDE_EFFECTS, UNCERTAIN
from ..core.formatting import fmt_addr, fmt_int
from . import theme
from .widgets import DataTable

SYMBOL_FILETYPES = [("ELF or GNU ld map", "*.elf *.axf *.out *.map"), ("Linker map", "*.map"),
                    ("ELF", "*.elf *.axf *.out"), ("All files", "*.*")]
ALL_MODULES = "(all modules)"
SHOW = ["Real changes", "Likely relocated (unverified)", "Relocated / moved only", "All symbols"]


class FeaturesView(ttk.Frame):
    """on_load_symbols(side, path|None) loads (or clears with None) the symbol file of a build."""

    def __init__(self, master, on_load_symbols, on_open_address):
        super().__init__(master, padding=(14, 12))
        self.on_load_symbols = on_load_symbols
        self.on_open_address = on_open_address
        self.session = None

        src = ttk.Frame(self)
        src.pack(fill="x")
        self.src_labels = {}
        for col, side in enumerate(("A", "B")):
            ttk.Label(src, text=f"Symbols {side}:").grid(row=0, column=col * 4, sticky="w", padx=(0 if col == 0 else 20, 4))
            lbl = ttk.Label(src, text="(none)", font=theme.mono_font(), width=34)
            lbl.grid(row=0, column=col * 4 + 1, sticky="w")
            self.src_labels[side] = lbl
            ttk.Button(src, text="Load ELF / .map…", command=lambda s=side: self._browse(s)).grid(
                row=0, column=col * 4 + 2, padx=3)
            ttk.Button(src, text="Clear", command=lambda s=side: self.on_load_symbols(s, None)).grid(
                row=0, column=col * 4 + 3)
        ttk.Label(self, style="Muted.TLabel", wraplength=1150, justify="left", text=(
            "A binary does not know about 'features'. The closest reliable mapping is the symbol information of "
            "each build: functions, variables and the source module (object file) they come from. ELF files carry "
            "symbols automatically; for .bin/.hex load the ELF or the GNU ld linker map (-Wl,-Map=app.map) of each "
            "build. Functions are compared by content. Calls and pointers to code that merely moved are decoded and "
            "recognised, so functions changed only by such address updates are reported as RELOCATED, not MODIFIED."
        )).pack(fill="x", pady=(4, 2))
        self.summary = ttk.Label(self, font=theme.bold_font())
        self.summary.pack(fill="x")
        self.notes = ttk.Label(self, style="Muted.TLabel", wraplength=1150, justify="left")
        self.notes.pack(fill="x")

        panes = ttk.PanedWindow(self, orient="vertical")
        panes.pack(fill="both", expand=True, pady=(4, 0))
        top = ttk.Frame(panes)
        ttk.Label(top, text="By module (select to filter the list below)", style="TLabelframe.Label").pack(anchor="w")
        self.modules = DataTable(top, [
            ("module", "Module", 180, "w"), ("modified", "Modified", 75, "e"), ("added", "Added", 65, "e"),
            ("removed", "Removed", 70, "e"), ("bytes", "Changed bytes", 105, "e"),
            ("status", "Status", 95, "w"), ("symbols", "Symbols", 420, "w")], height=6)
        self.modules.pack(fill="both", expand=True)
        self.modules.tree.bind("<<TreeviewSelect>>", self._module_selected)
        panes.add(top, weight=1)

        bottom = ttk.Frame(panes)
        bar = ttk.Frame(bottom)
        bar.pack(fill="x", pady=(4, 2))
        ttk.Label(bar, text="Functions / variables", style="TLabelframe.Label").pack(side="left")
        ttk.Label(bar, text="Show").pack(side="left", padx=(16, 3))
        self.show_box = ttk.Combobox(bar, values=SHOW, state="readonly", width=14)
        self.show_box.set(SHOW[0])
        self.show_box.pack(side="left")
        ttk.Label(bar, text="Module").pack(side="left", padx=(10, 3))
        self.module_box = ttk.Combobox(bar, values=[ALL_MODULES], state="readonly", width=20)
        self.module_box.set(ALL_MODULES)
        self.module_box.pack(side="left")
        ttk.Label(bar, text="Find").pack(side="left", padx=(10, 3))
        self.find_var = tk.StringVar()
        fe = ttk.Entry(bar, textvariable=self.find_var, width=22)
        fe.pack(side="left")
        for w in (self.show_box, self.module_box):
            w.bind("<<ComboboxSelected>>", lambda e: self.refresh_symbols())
        self.find_var.trace_add("write", lambda *a: self.refresh_symbols())
        ttk.Label(bar, text="Double-click to view in hex viewer", style="Muted.TLabel").pack(side="right")
        self.symbols = DataTable(bottom, [
            ("name", "Symbol", 200, "w"), ("kind", "Kind", 60, "w"), ("module", "Module", 110, "w"),
            ("status", "Status", 140, "w"), ("addr_a", "Addr A", 100, "w"), ("addr_b", "Addr B", 100, "w"),
            ("size_a", "Size A", 75, "e"), ("size_b", "Size B", 75, "e"), ("delta", "Δ size", 70, "e"),
            ("changed", "Changed bytes", 100, "e"), ("reloc", "Addr-ref bytes", 100, "e"),
            ("notes", "Notes", 300, "w")], height=10, row_limit=20000)
        self.symbols.pack(fill="both", expand=True)
        self.symbols.tree.bind("<Double-1>", self._open)
        self.symbols.tree.tag_configure("MODIFIED", foreground=theme.C_CHANGED)
        self.symbols.tree.tag_configure("MOVED", foreground=theme.C_NP)
        self.symbols.tree.tag_configure("RELOCATED", foreground=theme.C_NP)
        self.symbols.tree.tag_configure("LIKELY RELOCATED", foreground=theme.C_EXPECTED)
        self.symbols.tree.tag_configure("NOT IN IMAGE", foreground=theme.C_NP)
        panes.add(bottom, weight=3)
        self._rows = {}

    # --------------------------------------------------------------- loading
    def _browse(self, side: str) -> None:
        from .settings import get_settings
        settings = get_settings()
        path = filedialog.askopenfilename(parent=self, title=f"Symbols for Firmware {side} (ELF or GNU ld .map)",
                                          filetypes=SYMBOL_FILETYPES, initialdir=settings.last_dir)
        if path:
            settings.remember_file(path)
            self.on_load_symbols(side, path)

    def set_sources(self, source_a: str | None, source_b: str | None) -> None:
        self.src_labels["A"].configure(text=source_a or "(none)")
        self.src_labels["B"].configure(text=source_b or "(none)")

    # --------------------------------------------------------------- display
    def show(self, session) -> None:
        self.session = session
        fr = session.features if session is not None else None
        if session is not None:
            self.set_sources(session.symbols_a.source if session.symbols_a else None,
                             session.symbols_b.source if session.symbols_b else None)
        if fr is None:
            self.modules.clear()
            self.symbols.clear()
            self.module_box.configure(values=[ALL_MODULES])
            self.module_box.set(ALL_MODULES)
            if session is None:
                self.summary.configure(text="")
                self.notes.configure(text="")
            else:
                self.summary.configure(text="No symbol information - load the ELF or .map file of each build above.",
                                       foreground=theme.MUTED)
                self.notes.configure(text=session.features_error or "")
            return
        self.summary.configure(
            text=f"{fr.count('MODIFIED')} modified · {fr.count('ADDED')} added · {fr.count('REMOVED')} removed · "
                 f"{len(fr.modules)} module(s) edited   —   {fr.count('LIKELY RELOCATED')} likely relocated "
                 f"(unverified)   —   side effects: {fr.count('RELOCATED')} relocated, "
                 f"{fr.count('MOVED')} moved · "
                 f"{fr.unattributed_bytes:,} changed bytes outside known symbols",
            foreground=theme.C_CHANGED if fr.changed else theme.C_SAME)
        self.notes.configure(text="\n".join(fr.notes))
        self.modules.set_rows([
            (f"m{i}", (m.module, m.modified, m.added, m.removed, fmt_int(m.changed_bytes),
                       "EXPECTED" if m.expected_only else "CHANGED", ", ".join(m.symbols)),
             ("EXPECTED" if m.expected_only else "CHANGED",),
             (m.module, m.modified, m.added, m.removed, m.changed_bytes, m.expected_only, ""))
            for i, m in enumerate(fr.modules)])
        mods = sorted({c.module or "(unknown module)" for c in fr.symbols})
        self.module_box.configure(values=[ALL_MODULES] + mods)
        if self.module_box.get() not in mods:
            self.module_box.set(ALL_MODULES)
        self.refresh_symbols()

    def _module_selected(self, _e=None) -> None:
        iid = self.modules.selected_iid()
        if iid and self.session is not None and self.session.features is not None:
            self.module_box.set(self.session.features.modules[int(iid[1:])].module)
            self.refresh_symbols()

    def refresh_symbols(self) -> None:
        fr = self.session.features if self.session is not None else None
        if fr is None:
            return
        show = self.show_box.get()
        module = self.module_box.get()
        needle = self.find_var.get().strip().lower()
        rows = []
        self._rows = {}
        for i, c in enumerate(fr.symbols):
            if show == SHOW[0] and c.status not in REAL_CHANGES:
                continue
            if show == SHOW[1] and c.status not in UNCERTAIN:
                continue
            if show == SHOW[2] and c.status not in SIDE_EFFECTS:
                continue
            if module != ALL_MODULES and (c.module or "(unknown module)") != module:
                continue
            if needle and needle not in c.name.lower():
                continue
            status = "MODIFIED (expected)" if c.expected_only else c.status
            tag = "EXPECTED" if c.expected_only else c.status
            self._rows[str(i)] = c
            rows.append((str(i), (c.name, c.kind, c.module or "-", status, fmt_addr(c.addr_a), fmt_addr(c.addr_b),
                                  fmt_int(c.size_a), fmt_int(c.size_b), f"{c.size_delta:+,}" if c.size_delta else "",
                                  fmt_int(c.changed_bytes), fmt_int(c.reloc_bytes) if c.reloc_bytes else "",
                                  "; ".join(c.notes)), (tag,),
                         (c.name, c.kind, c.module, status, c.addr_a, c.addr_b, c.size_a, c.size_b, c.size_delta,
                          c.changed_bytes, c.reloc_bytes, "")))
        self.symbols.set_rows(rows)

    def _open(self, _e=None) -> None:
        c = self._rows.get(self.symbols.selected_iid() or "")
        if c is None or self.session is None:
            return
        addr = c.addr_b if c.addr_b is not None else c.addr_a
        if addr is not None and self.session.diff.union.contains(addr):
            first = self.session.diff.diff.first_at_or_after(addr)
            size = c.size_b or c.size_a or 1
            self.on_open_address(first if first is not None and first < addr + size else addr)
