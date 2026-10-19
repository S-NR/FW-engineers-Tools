"""Editor for memory regions, expected-difference areas and project settings."""

from __future__ import annotations

import copy
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ..core.formatting import fmt_size, parse_int
from ..core.region_manager import MemoryRegion, ProjectConfig, parse_range_text
from ..errors import ConfigError
from . import theme
from .widgets import DataTable, tip

COLS = [("name", "Name", 150, "w"), ("start", "Start", 110, "w"), ("end", "End (incl.)", 110, "w"),
        ("size", "Size", 90, "e"), ("desc", "Description", 220, "w")]


class RegionDialog(tk.Toplevel):
    """Modal dialog to add/edit one region. ``result`` is a MemoryRegion or None."""

    def __init__(self, master, title: str, region: MemoryRegion | None, existing_names: set[str]):
        super().__init__(master)
        self.title(title)
        self.transient(master.winfo_toplevel())
        self.resizable(False, False)
        self.configure(background=theme.BG)
        self.result: MemoryRegion | None = None
        self.existing = {n.lower() for n in existing_names}
        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        self.vars = {k: tk.StringVar() for k in ("name", "start", "end", "desc")}
        if region:
            self.vars["name"].set(region.name)
            self.vars["start"].set(f"0x{region.start:08X}")
            self.vars["end"].set(f"0x{region.end:08X}")
            self.vars["desc"].set(region.description)
        rows = [("Name", "name", ""), ("Start address", "start", "e.g. 0x08000000"),
                ("End address (inclusive)", "end", "e.g. 0x08007FFF, or +size such as +0x8000"),
                ("Description", "desc", "optional")]
        first = None
        for i, (label, key, hint) in enumerate(rows):
            ttk.Label(frm, text=label).grid(row=i * 2, column=0, sticky="w", pady=(4, 0))
            ent = ttk.Entry(frm, textvariable=self.vars[key], width=34,
                            font=theme.mono_font() if key in ("start", "end") else None)
            ent.grid(row=i * 2, column=1, sticky="we", padx=(8, 0), pady=(4, 0))
            if hint:
                ttk.Label(frm, text=hint, style="Muted.TLabel").grid(row=i * 2 + 1, column=1, sticky="w", padx=(8, 0))
            first = first or ent
        self.err = ttk.Label(frm, style="Warn.TLabel", wraplength=360)
        self.err.grid(row=9, column=0, columnspan=2, sticky="w", pady=(8, 0))
        btns = ttk.Frame(frm)
        btns.grid(row=10, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(btns, text="OK", command=self._ok, style="Accent.TButton").pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="left")
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())
        first.focus_set()
        self.grab_set()
        self.wait_window()

    def _ok(self) -> None:
        name = self.vars["name"].get().strip()
        if not name:
            self.err.configure(text="Name is required.")
            return
        if name.lower() in self.existing:
            self.err.configure(text=f"A region named '{name}' already exists.")
            return
        try:
            start, end = parse_range_text(self.vars["start"].get(), self.vars["end"].get())
        except ValueError as exc:
            self.err.configure(text=str(exc))
            return
        if end >= 1 << 64:
            self.err.configure(text="End address exceeds 64-bit range.")
            return
        self.result = MemoryRegion(name, start, end, self.vars["desc"].get().strip())
        self.destroy()


class RegionListEditor(ttk.LabelFrame):
    def __init__(self, master, title: str, kind: str, on_change):
        super().__init__(master, text=f"  {title}  ", padding=(12, 8))
        self.kind = kind
        self.on_change = on_change
        self.items: list[MemoryRegion] = []
        self.table = DataTable(self, COLS, height=6)
        self.table.pack(side="left", fill="both", expand=True)
        self.table.tree.bind("<Double-1>", lambda e: self.edit())
        btns = ttk.Frame(self)
        btns.pack(side="left", fill="y", padx=(6, 0))
        for text, cmd in (("Add…", self.add), ("Edit…", self.edit), ("Remove", self.remove),
                          ("Move up", lambda: self.move(-1)), ("Move down", lambda: self.move(1))):
            ttk.Button(btns, text=text, command=cmd, width=11).pack(pady=2)

    def set_items(self, items: list[MemoryRegion]) -> None:
        self.items = list(items)
        self._render()

    def _render(self) -> None:
        rows = [(str(i), (r.name, f"0x{r.start:08X}", f"0x{r.end:08X}", fmt_size(r.size), r.description), (),
                 (r.name, r.start, r.end, r.size, r.description)) for i, r in enumerate(self.items)]
        self.table.set_rows(rows)

    def _selected(self) -> int | None:
        iid = self.table.selected_iid()
        return int(iid) if iid is not None else None

    def add(self) -> None:
        dlg = RegionDialog(self, f"Add {self.kind}", None, {r.name for r in self.items})
        if dlg.result:
            self.items.append(dlg.result)
            self._render()
            self.on_change()

    def edit(self) -> None:
        i = self._selected()
        if i is None:
            return
        others = {r.name for j, r in enumerate(self.items) if j != i}
        dlg = RegionDialog(self, f"Edit {self.kind}", self.items[i], others)
        if dlg.result:
            self.items[i] = dlg.result
            self._render()
            self.on_change()

    def remove(self) -> None:
        i = self._selected()
        if i is None:
            return
        if messagebox.askyesno("Remove", f"Remove {self.kind.lower()} '{self.items[i].name}'?", parent=self):
            del self.items[i]
            self._render()
            self.on_change()

    def move(self, delta: int) -> None:
        i = self._selected()
        if i is None or not 0 <= i + delta < len(self.items):
            return
        self.items[i], self.items[i + delta] = self.items[i + delta], self.items[i]
        self._render()
        self.table.tree.selection_set(str(i + delta))
        self.on_change()


class ConfigEditor(ttk.Frame):
    """on_change(config, recompare: bool, base_changed: bool) is called after every edit."""

    def __init__(self, master, on_change):
        super().__init__(master, padding=(14, 12))
        self.on_change = on_change
        self.config = ProjectConfig()
        self.path: str | None = None

        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Label(top, text="Project").pack(side="left")
        self.name_var = tk.StringVar()
        ne = ttk.Entry(top, textvariable=self.name_var, width=36)
        ne.pack(side="left", padx=4)
        ne.bind("<FocusOut>", lambda e: setattr(self.config, "name", self.name_var.get().strip()))
        ttk.Button(top, text="Load configuration…", command=self.load_dialog).pack(side="left", padx=(10, 2))
        ttk.Button(top, text="Save", command=self.save).pack(side="left", padx=2)
        ttk.Button(top, text="Save as…", command=self.save_as).pack(side="left", padx=2)
        ttk.Button(top, text="Import expected areas…", command=self.import_expected).pack(side="left", padx=(10, 2))
        self.path_label = ttk.Label(top, style="Muted.TLabel")
        self.path_label.pack(side="left", padx=10)

        opts = ttk.LabelFrame(self, text="  Comparison settings  ", padding=(12, 8))
        opts.pack(fill="x", pady=(10, 10))
        ttk.Label(opts, text="Default .bin base address").grid(row=0, column=0, sticky="w")
        self.base_var = tk.StringVar(value="0x00000000")
        tip(ttk.Entry(opts, textvariable=self.base_var, width=14, font=theme.mono_font()),
            "MCU address of file offset 0 for raw .bin files (HEX / ELF carry their own addresses)").grid(
            row=0, column=1, sticky="w", padx=(8, 24))
        ttk.Label(opts, text="Block merge gap (bytes)").grid(row=0, column=2, sticky="w")
        self.gap_var = tk.StringVar(value="0")
        tip(ttk.Entry(opts, textvariable=self.gap_var, width=8, font=theme.mono_font()),
            "Join changed runs separated by at most N unchanged bytes into one block").grid(
            row=0, column=3, sticky="w", padx=(8, 24))
        ttk.Button(opts, text="Apply settings", style="Accent.TButton", command=self.apply_settings).grid(
            row=0, column=5, rowspan=2, sticky="e")
        opts.columnconfigure(4, weight=1)
        self.fill_on = tk.BooleanVar(value=False)
        fill = ttk.Frame(opts)
        fill.grid(row=1, column=0, columnspan=4, sticky="w", pady=(8, 0))
        tip(ttk.Checkbutton(fill, text="Compare bytes present in only one image against fill value",
                            variable=self.fill_on),
            "Off by default: bytes present in only one image are always reported as differences. Turn on (e.g. "
            "0xFF = erased flash) to compare a sparse HEX file with a full BIN dump.").pack(side="left")
        self.fill_var = tk.StringVar(value="0xFF")
        ttk.Entry(fill, textvariable=self.fill_var, width=6, font=theme.mono_font()).pack(side="left", padx=8)
        ttk.Label(opts, style="Muted.TLabel", wraplength=1150, justify="left",
                  text="Base address = MCU address of file offset 0 for raw .bin files (HEX/ELF carry their own "
                       "addresses).  Merge gap joins changed runs separated by ≤ N unchanged bytes.  "
                       "Fill mode is off by default: one-sided bytes are always reported as differences."
                  ).grid(row=2, column=0, columnspan=6, sticky="w", pady=(8, 0))

        panes = ttk.Frame(self)
        panes.pack(fill="both", expand=True)
        self.regions = RegionListEditor(panes, "Memory regions", "Region", self._changed)
        self.regions.pack(fill="both", expand=True, pady=(0, 6))
        self.expected = RegionListEditor(
            panes, "Expected / ignored difference areas (changes here are reported as EXPECTED)",
            "Expected area", self._changed)
        self.expected.pack(fill="both", expand=True)
        self.msg = ttk.Label(self, style="Warn.TLabel", wraplength=1100, justify="left")
        self.msg.pack(fill="x", pady=(4, 0))
        self.set_config(ProjectConfig())

    # ----------------------------------------------------------------- state
    def set_config(self, cfg: ProjectConfig, path: str | None = None) -> None:
        self.config = copy.deepcopy(cfg)
        self.path = path
        self.name_var.set(cfg.name)
        self.base_var.set(f"0x{(cfg.base_address or 0):08X}")
        self.gap_var.set(str(cfg.merge_gap))
        self.fill_on.set(cfg.gap_fill is not None)
        self.fill_var.set(f"0x{cfg.gap_fill if cfg.gap_fill is not None else 0xFF:02X}")
        self.regions.set_items(cfg.regions)
        self.expected.set_items(cfg.expected)
        self.path_label.configure(text=os.path.basename(path) if path else "(unsaved)")
        self._validate()

    def current_config(self) -> ProjectConfig:
        cfg = copy.deepcopy(self.config)
        cfg.name = self.name_var.get().strip()
        cfg.regions = list(self.regions.items)
        cfg.expected = list(self.expected.items)
        return cfg

    def _validate(self) -> bool:
        cfg = self.current_config()
        errors, warnings = cfg.validate()
        lines = [f"ERROR: {e}" for e in errors] + [f"Warning: {w}" for w in warnings]
        self.msg.configure(text="\n".join(lines), foreground=theme.C_CHANGED if errors else theme.C_EXPECTED)
        return not errors

    def _changed(self) -> None:
        if self._validate():
            self.config = self.current_config()
            self.on_change(self.config, False, False)

    def apply_settings(self) -> None:
        try:
            base = parse_int(self.base_var.get())
            gap = parse_int(self.gap_var.get())
            fill = parse_int(self.fill_var.get()) if self.fill_on.get() else None
            if fill is not None and fill > 0xFF:
                raise ValueError("fill value must be 0x00-0xFF")
        except ValueError as exc:
            messagebox.showerror("Invalid setting", str(exc), parent=self)
            return
        old = self.config
        cfg = self.current_config()
        cfg.base_address, cfg.merge_gap, cfg.gap_fill = base, gap, fill
        base_changed = (old.base_address or 0) != base
        recompare = old.gap_fill != fill
        self.config = cfg
        if self._validate():
            self.on_change(cfg, recompare, base_changed)

    # ------------------------------------------------------------------ files
    def load_dialog(self) -> None:
        from .settings import get_settings
        path = filedialog.askopenfilename(parent=self, title="Load region / project configuration",
                                          filetypes=[("JSON configuration", "*.json"), ("All files", "*.*")],
                                          initialdir=get_settings().last_dir)
        if path:
            self.load(path)

    def load(self, path: str) -> bool:
        try:
            cfg = ProjectConfig.load(path)
        except ConfigError as exc:
            messagebox.showerror("Configuration error", str(exc), parent=self)
            return False
        old_base, old_fill = self.config.base_address or 0, self.config.gap_fill
        self.set_config(cfg, path)
        if self._validate():
            self.on_change(self.config, old_fill != cfg.gap_fill, old_base != (cfg.base_address or 0))
        return True

    def import_expected(self) -> None:
        path = filedialog.askopenfilename(parent=self, title="Import expected-difference areas",
                                          filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            other = ProjectConfig.load(path)
        except ConfigError as exc:
            messagebox.showerror("Configuration error", str(exc), parent=self)
            return
        names = {r.name.lower() for r in self.expected.items}
        added = [r for r in other.expected if r.name.lower() not in names]
        self.expected.set_items(self.expected.items + added)
        skipped = len(other.expected) - len(added)
        if skipped:
            messagebox.showwarning("Import", f"{skipped} area(s) skipped because the name already exists.", parent=self)
        self._changed()

    def save(self) -> None:
        if not self.path:
            self.save_as()
            return
        self._write(self.path)

    def save_as(self) -> None:
        path = filedialog.asksaveasfilename(parent=self, title="Save configuration", defaultextension=".json",
                                            filetypes=[("JSON configuration", "*.json")])
        if path:
            self._write(path)

    def _write(self, path: str) -> None:
        cfg = self.current_config()
        if not self._validate():
            messagebox.showerror("Cannot save", "Fix the configuration errors first.", parent=self)
            return
        try:
            cfg.save(path)
        except OSError as exc:
            messagebox.showerror("Save failed", str(exc), parent=self)
            return
        self.path = path
        self.path_label.configure(text=os.path.basename(path))
