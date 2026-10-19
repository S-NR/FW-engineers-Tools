"""Main application window."""

from __future__ import annotations

import os
import sys
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

from .. import APP_NAME, __version__
from ..core.binary_reader import identify_with_symbols, load_firmware
from ..core.region_manager import ProjectConfig
from ..core.session import build_session, default_symbols
from ..core.symbols import load_symbol_table
from ..errors import ComparisonCancelled, FirmwareToolError
from . import theme
from .comparison_view import BlockTable, RegionTable, SummaryPanel
from .config_editor import ConfigEditor
from .details_view import ChecksumView, ElfView, MetadataView
from .features_view import FeaturesView
from .file_panel import FilePanel
from .hex_viewer import HexComparisonWindow
from .memory_map import MemoryMapView
from .settings import get_settings
from .widgets import DataTable, TaskRunner, tip


class MainWindow:
    def __init__(self, root: tk.Tk, dnd_files=None):
        self.root = root
        self.settings = get_settings()
        root.title(APP_NAME)
        root.minsize(1100, 720)
        try:
            root.geometry(self.settings.geometry or "1440x940")
        except tk.TclError:
            root.geometry("1440x940")
        theme.apply_theme(root)
        try:
            self._icon = theme.app_icon(root)
            root.iconphoto(True, self._icon)
        except tk.TclError:
            pass
        self.images = {"A": None, "B": None}
        self.session = None
        self.hex_window: HexComparisonWindow | None = None
        self.runner = TaskRunner(root)
        self.config = ProjectConfig()
        self.symbol_tables = {"A": None, "B": None}  # user-supplied ELF/.map symbols
        self.elf_vaddr = tk.BooleanVar(value=False)
        self._queue: list = []  # pending load/compare actions while the worker is busy
        self._build_menu()
        self._build_layout(dnd_files)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ------------------------------------------------------------------ build
    def _build_menu(self) -> None:
        m = tk.Menu(self.root)
        f = tk.Menu(m, tearoff=False)
        f.add_command(label="Open Firmware A (old)…", accelerator="Ctrl+1", command=lambda: self.panel_a.browse())
        f.add_command(label="Open Firmware B (new)…", accelerator="Ctrl+2", command=lambda: self.panel_b.browse())
        f.add_command(label="Swap A and B", command=self.swap)
        self.recent_menu = tk.Menu(f, tearoff=False, postcommand=self._fill_recent_menu)
        f.add_cascade(label="Recent comparisons", menu=self.recent_menu)
        f.add_separator()
        f.add_command(label="Load configuration…", command=lambda: self.config_editor.load_dialog())
        f.add_command(label="Save configuration", command=lambda: self.config_editor.save())
        f.add_command(label="Save configuration as…", command=lambda: self.config_editor.save_as())
        f.add_separator()
        f.add_command(label="Export HTML report…", accelerator="Ctrl+R", command=self.export_html)
        f.add_command(label="Export changed blocks (CSV)…", command=self.export_csv)
        f.add_separator()
        f.add_command(label="Exit", command=self.on_close)
        m.add_cascade(label="File", menu=f)
        c = tk.Menu(m, tearoff=False)
        c.add_command(label="Compare", accelerator="F5", command=self.compare)
        c.add_command(label="Open hex comparison", accelerator="Ctrl+H", command=lambda: self.open_hex())
        c.add_separator()
        c.add_checkbutton(label="ELF: use virtual addresses (VMA) instead of load addresses (LMA)",
                          variable=self.elf_vaddr, command=self._reload_elf)
        m.add_cascade(label="Compare", menu=c)
        v = tk.Menu(m, tearoff=False)
        for i, (label, attr) in enumerate((("Files", "files_tab"), ("Configuration", "config_editor"),
                                           ("Summary", "summary_tab"), ("Region Comparison", "region_table"),
                                           ("Changed Blocks", "block_table"), ("Changed Functions", "features_view"),
                                           ("Memory Map", "memory_map"))):
            v.add_command(label=label, accelerator=f"Alt+{i + 1}",
                          command=lambda a=attr: self.nb.select(getattr(self, a)))
            self.root.bind(f"<Alt-Key-{i + 1}>", lambda e, a=attr: self.nb.select(getattr(self, a)))
        m.add_cascade(label="View", menu=v)
        h = tk.Menu(m, tearoff=False)
        h.add_command(label="Quick start", command=self.quick_start)
        h.add_command(label="Keyboard shortcuts", command=self.shortcuts)
        h.add_separator()
        h.add_command(label="About", command=self.about)
        m.add_cascade(label="Help", menu=h)
        self.root.configure(menu=m)
        self.root.bind("<Control-Key-1>", lambda e: self.panel_a.browse())
        self.root.bind("<Control-Key-2>", lambda e: self.panel_b.browse())
        self.root.bind("<F5>", lambda e: self.compare())
        self.root.bind("<Control-h>", lambda e: self.open_hex())
        self.root.bind("<Control-r>", lambda e: self.export_html())

    def _build_layout(self, dnd_files) -> None:
        # HEADER: app identity, files being compared, verdict badge (visible on every tab)
        header = tk.Frame(self.root, background=theme.HEADER_BG, padx=16, pady=10)
        header.pack(side="top", fill="x")
        if getattr(self, "_icon", None) is not None:
            tk.Label(header, image=self._icon, background=theme.HEADER_BG).pack(side="left", padx=(0, 10))
        tk.Label(header, text=APP_NAME, font=theme.title_font(), background=theme.HEADER_BG,
                 foreground=theme.HEADER_FG).pack(side="left")
        tk.Label(header, text=f"  v{__version__}", font=theme.small_font(), background=theme.HEADER_BG,
                 foreground=theme.HEADER_MUTED).pack(side="left", pady=(3, 0))
        self.header_verdict = tk.Label(header, font=theme.bold_font(), padx=12, pady=3,
                                       background=theme.HEADER_BG, foreground=theme.HEADER_FG)
        self.header_verdict.pack(side="right")
        files = tk.Frame(header, background=theme.HEADER_BG)
        files.pack(side="right", padx=(0, 18))
        self.header_parts = {}
        for side, color in (("A", "#93c5fd"), ("B", "#c4b5fd")):
            if side == "B":
                tk.Label(files, text="  →  ", background=theme.HEADER_BG, foreground=theme.HEADER_MUTED,
                         font=theme.bold_font()).pack(side="left")
            tk.Label(files, text=f" {side} ", background=color, foreground=theme.HEADER_BG,
                     font=theme.bold_font()).pack(side="left", padx=(0, 6))
            lbl = tk.Label(files, text="not loaded", background=theme.HEADER_BG, foreground=theme.HEADER_FG,
                           font=theme.ui_font())
            lbl.pack(side="left")
            self.header_parts[side] = lbl
        self.header_files = files

        # BOTTOM (packed before the notebook so it always stays visible)
        bottom = ttk.Frame(self.root, padding=(12, 8), style="Bottom.TFrame")
        bottom.pack(side="bottom", fill="x")
        tip(ttk.Button(bottom, text="Open Hex Comparison", style="Accent.TButton", command=lambda: self.open_hex()),
            "Side-by-side byte view with navigation between differences (Ctrl+H)").pack(side="left")
        tip(ttk.Button(bottom, text="HTML Report…", command=self.export_html),
            "Save a self-contained engineering report (Ctrl+R)").pack(side="left", padx=(8, 0))
        tip(ttk.Button(bottom, text="CSV Export…", command=self.export_csv),
            "Export every changed block as CSV").pack(side="left", padx=(8, 0))
        self.cancel_btn = ttk.Button(bottom, text="Cancel", command=self.cancel, state="disabled")
        self.cancel_btn.pack(side="right")
        self.progress = ttk.Progressbar(bottom, length=200, mode="determinate")
        self.progress.pack(side="right", padx=10)
        self.status = ttk.Label(bottom, text="Ready - start by choosing Firmware A and B.", style="Bottom.TLabel")
        self.status.pack(side="left", padx=16, fill="x", expand=True)

        # TABS: setup (Files, Configuration) then results
        self.nb = ttk.Notebook(self.root)
        self.nb.pack(side="top", fill="both", expand=True, padx=10, pady=(8, 6))
        self.files_tab = self._build_files_tab(self.nb, dnd_files)
        self.config_editor = ConfigEditor(self.nb, self.on_config_changed)
        self.summary_tab = self._build_summary_tab(self.nb)
        self.region_table = RegionTable(self.nb, self.open_hex, lambda: self.nb.select(self.config_editor))
        self.block_table = BlockTable(self.nb, self.open_hex, self.export_csv)
        self.features_view = FeaturesView(self.nb, self.load_symbols, self.open_hex)
        self.memory_map = MemoryMapView(self.nb, self.open_hex)
        self.metadata_view = MetadataView(self.nb, self.open_hex)
        self.checksum_view = ChecksumView(self.nb, lambda: (self.images["A"], self.images["B"]),
                                          lambda: self.config.regions)
        self.elf_view = ElfView(self.nb)
        for widget, text in ((self.files_tab, "1  Files"), (self.config_editor, "2  Configuration"),
                             (self.summary_tab, "Summary"), (self.region_table, "Regions"),
                             (self.block_table, "Changed Blocks"), (self.features_view, "Changed Functions"),
                             (self.memory_map, "Memory Map"), (self.metadata_view, "Metadata"),
                             (self.checksum_view, "Checksums"), (self.elf_view, "ELF")):
            self.nb.add(widget, text=text)
        self.nb.bind("<<NotebookTabChanged>>", lambda e: self._refresh_setup_labels())
        self._refresh_setup_labels()

    def _build_files_tab(self, master, dnd_files) -> ttk.Frame:
        tab = ttk.Frame(master, padding=(14, 12))
        files = ttk.Frame(tab)
        files.pack(fill="x")
        self.panel_a = FilePanel(files, "Firmware A  ·  old", self.load_file, dnd_files, side="A")
        self.panel_b = FilePanel(files, "Firmware B  ·  new", self.load_file, dnd_files, side="B")
        self.panel_a.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.panel_b.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        files.columnconfigure(0, weight=1, uniform="fw")
        files.columnconfigure(1, weight=1, uniform="fw")

        action = ttk.Frame(tab)
        action.pack(fill="x", pady=(12, 0))
        tip(ttk.Button(action, text="Compare  ▶", style="Accent.TButton", command=self.compare),
            "Run the comparison (F5). It also runs automatically once both files are loaded.").pack(side="left")
        tip(ttk.Button(action, text="↔  Swap A / B", command=self.swap),
            "Exchange old and new firmware").pack(side="left", padx=(8, 0))
        ttk.Label(action, style="Muted.TLabel", text="Results open on the Summary tab automatically.").pack(
            side="left", padx=14)

        row = ttk.Frame(tab)
        row.pack(fill="x", pady=(12, 0))
        syms = ttk.LabelFrame(row, text="  Symbol files  (optional — shows which functions changed)  ",
                              padding=(12, 8))
        syms.pack(side="left", fill="both", expand=True, padx=(0, 8))
        self.symbol_labels = {}
        for r, side in enumerate(("A", "B")):
            color = theme.A_COLOR if side == "A" else theme.B_COLOR
            tk.Label(syms, text=f" {side} ", background=color, foreground="#ffffff",
                     font=theme.bold_font()).grid(row=r, column=0, sticky="w", pady=3)
            lbl = ttk.Label(syms, text="(none)", font=theme.mono_font(), width=1)
            lbl.grid(row=r, column=1, sticky="ew", padx=10)
            self.symbol_labels[side] = lbl
            tip(ttk.Button(syms, text="Load ELF / .map…", command=lambda s=side: self.features_view._browse(s)),
                "ELF or GNU ld linker map of this build. ELF files compared directly already carry symbols."
                ).grid(row=r, column=2, padx=(0, 6))
            ttk.Button(syms, text="Clear", command=lambda s=side: self.load_symbols(s, None)).grid(row=r, column=3)
        syms.columnconfigure(1, weight=1)

        cfg = ttk.LabelFrame(row, text="  Configuration  ", padding=(12, 8))
        cfg.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.config_label = ttk.Label(cfg, font=theme.mono_font(), width=1)
        self.config_label.pack(fill="x", pady=(2, 8))
        btns = ttk.Frame(cfg)
        btns.pack(fill="x")
        tip(ttk.Button(btns, text="Load configuration…", command=lambda: self.config_editor.load_dialog()),
            "Project file with memory regions and expected-difference areas (.json)").pack(side="left")
        ttk.Button(btns, text="Edit regions…", command=lambda: self.nb.select(self.config_editor)).pack(
            side="left", padx=(8, 0))

        ctrl = ttk.LabelFrame(tab, text="  Controller identification  (heuristic — evidence listed)  ",
                              padding=(12, 8))
        ctrl.pack(fill="both", expand=True, pady=(12, 0))
        self.controller_warn = ttk.Label(ctrl, style="Warn.TLabel")
        self.controller_warn.pack(fill="x")
        self.controller_table = DataTable(ctrl, [
            ("fw", "Firmware", 80, "center"), ("item", "Item / evidence source", 240, "w"),
            ("value", "Value / finding", 760, "w"), ("conf", "Confidence", 100, "w")], height=6,
            empty_text="Load a firmware file to identify its controller.")
        self.controller_table.pack(fill="both", expand=True)
        return tab

    def _build_summary_tab(self, master) -> ttk.Frame:
        tab = ttk.Frame(master, padding=(14, 12))
        self.summary = SummaryPanel(tab)
        self.summary.pack(fill="x")
        nav = ttk.Frame(tab)
        nav.pack(fill="x", pady=(12, 0))
        ttk.Label(nav, text="Explore:", style="Section.TLabel").pack(side="left", padx=(0, 10))
        for text, target in (("Regions", lambda: self.region_table),
                             ("Changed Blocks", lambda: self.block_table),
                             ("Changed Functions", lambda: self.features_view),
                             ("Memory Map", lambda: self.memory_map),
                             ("Metadata", lambda: self.metadata_view)):
            ttk.Button(nav, text=text, command=lambda t=target: self.nb.select(t())).pack(side="left", padx=(0, 6))
        ttk.Button(nav, text="Open Hex Comparison", style="Accent.TButton",
                   command=lambda: self.open_hex()).pack(side="right")

        what = ttk.LabelFrame(tab, text="  What changed  ·  functions and variables  ", padding=(12, 8))
        what.pack(fill="both", expand=True, pady=(12, 0))
        self.what_note = ttk.Label(what, style="Muted.TLabel", wraplength=1200, justify="left")
        self.what_note.pack(fill="x", pady=(0, 6))
        self.what_table = DataTable(what, [
            ("status", "Status", 120, "w"), ("name", "Symbol", 240, "w"), ("kind", "Kind", 70, "w"),
            ("module", "Module", 160, "w"), ("bytes", "Changed bytes", 110, "e"), ("notes", "Notes", 520, "w")],
            height=8, row_limit=500, empty_text="No function-level changes to show.")
        self.what_table.pack(fill="both", expand=True)
        self.what_table.tree.bind("<Double-1>", self._open_what)
        self._what_rows = {}
        return tab

    def _set_header_verdict(self, kind: str | None, text: str = "") -> None:
        if not kind:
            self.header_verdict.configure(text="", background=theme.HEADER_BG)
            return
        fg, bg, icon = theme.VERDICT_STYLE.get(kind, theme.VERDICT_STYLE["NONE"])
        self.header_verdict.configure(text=f"{icon}  {text or kind}", background=bg, foreground=fg)

    def _refresh_controller_view(self) -> None:
        rows = []
        idents = {}
        for side in ("A", "B"):
            img = self.images[side]
            m = img.mcu if img is not None else None
            if m is None:
                continue
            idents[side] = m
            n = len(rows)
            rows.append((f"{side}{n}", (side, "Identified controller", m.short(), m.confidence), (),
                         (side, 0, m.short(), m.confidence)))
            for key, val in m.details.items():
                n = len(rows)
                rows.append((f"{side}{n}", (side, key, val, ""), (), (side, 1, val, "")))
            for ev in m.evidence:
                n = len(rows)
                tag = ("CHANGED",) if ev.finding.startswith("WARNING") else ()
                rows.append((f"{side}{n}", (side, ev.source, ev.finding, ev.confidence), tag,
                             (side, 2, ev.finding, ev.confidence)))
        self.controller_table.set_rows(rows)
        warn = ""
        if len(idents) == 2 and idents["A"].same_target_as(idents["B"]) is False:
            warn = (f"⚠  Firmware A and B appear to target different controllers: "
                    f"{idents['A'].device or idents['A'].family} vs {idents['B'].device or idents['B'].family}")
        self.controller_warn.configure(text=warn)
        if warn:
            self._set_header_verdict("WARNING", "DIFFERENT CONTROLLERS?")

    def _show_what_changed(self) -> None:
        s = self.session
        self._what_rows = {}
        if s is None:
            self.what_table.clear()
            self.what_note.configure(text="")
            return
        fr = s.features
        if fr is None:
            self.what_table.clear()
            self.what_note.configure(text="No symbol information. Load the ELF or linker .map of each build on the "
                                          "Files tab to see which functions changed.")
            return
        rows = []
        for i, c in enumerate(fr.changed + fr.uncertain):
            status = "MODIFIED (expected)" if c.expected_only else c.status
            self._what_rows[str(i)] = c
            rows.append((str(i), (status, c.name, c.kind, c.module or "-", f"{c.changed_bytes:,}", "; ".join(c.notes)),
                         ("EXPECTED" if c.expected_only else c.status,),
                         (status, c.name, c.kind, c.module, c.changed_bytes, "")))
        self.what_table.set_rows(rows)
        self.what_note.configure(text=f"{fr.headline()}.  Functions that only moved or had call targets / pointers "
                                      "updated are side effects and are listed on the Changed Functions tab.  "
                                      "Double-click a row to open it in the hex viewer.")

    def _open_what(self, _e=None) -> None:
        c = self._what_rows.get(self.what_table.selected_iid() or "")
        if c is not None and self.session is not None:
            addr = c.addr_b if c.addr_b is not None else c.addr_a
            if addr is not None and self.session.diff.union.contains(addr):
                self.open_hex(addr)

    def _refresh_setup_labels(self) -> None:
        """Header, symbol-file and configuration labels."""
        a, b = self.images["A"], self.images["B"]
        names = [img.name if img is not None else "not loaded" for img in (a, b)]
        if a is not None and b is not None and a.name == b.name:  # same file name: show the folder too
            names = [f"{os.path.basename(os.path.dirname(img.path))}/{img.name}" for img in (a, b)]
        for side, name in zip("AB", names):
            self.header_parts[side].configure(text=name)
        if self.session is not None:
            self._set_header_verdict(self.session.analysis.verdict)
        else:
            self._set_header_verdict(None)
        for side, img in (("A", a), ("B", b)):
            table = self.symbol_tables[side]
            if table is not None:
                text = f"{table.source}  ·  {len(table):,} symbols"
            elif img is not None and img.elf is not None:
                text = f"from {img.name} (ELF)"
            else:
                text = "none"
            self.symbol_labels[side].configure(text=text)
        self._refresh_controller_view()
        cfg = self.config
        path = self.config_editor.path if hasattr(self, "config_editor") else None
        self.config_label.configure(
            text=f"{cfg.name or 'Unnamed project'}  ·  {len(cfg.regions)} region(s)  ·  "
                 f"{len(cfg.expected)} expected area(s)  ·  " + (os.path.basename(path) if path else "not saved"))

    # ------------------------------------------------------------- recent / help
    def _fill_recent_menu(self) -> None:
        self.recent_menu.delete(0, "end")
        recent = self.settings.recent
        if not recent:
            self.recent_menu.add_command(label="(no recent comparisons)", state="disabled")
            return
        for r in recent:
            label = f"{os.path.basename(r['a'])}  →  {os.path.basename(r['b'])}    ({os.path.dirname(r['b'])})"
            self.recent_menu.add_command(label=label, command=lambda e=r: self.open_recent(e))

    def open_recent(self, entry: dict) -> None:
        missing = [p for p in (entry["a"], entry["b"]) if not os.path.isfile(p)]
        if missing:
            messagebox.showerror("File not found", "These files no longer exist:\n" + "\n".join(missing),
                                 parent=self.root)
            return
        for side in ("A", "B"):
            sym = entry.get(f"symbols_{side.lower()}")
            self.load_symbols(side, sym if sym and os.path.isfile(sym) else None)
        if entry.get("config") and os.path.isfile(entry["config"]):
            self.config_editor.load(entry["config"])
        self.nb.select(self.files_tab)
        self._run_or_queue(lambda: self.load_file(self.panel_a, entry["a"]))
        self._queue.append(lambda: self.load_file(self.panel_b, entry["b"]))

    def quick_start(self) -> None:
        messagebox.showinfo("Quick start", (
            "1.  Files tab: choose Firmware A (old) and Firmware B (new) - click a drop area or drag a file onto it.\n"
            "     For raw .bin files set the base address (e.g. 0x08000000).\n\n"
            "2.  Optional: load the ELF or linker .map of each build to see which functions changed.\n\n"
            "3.  Optional: Configuration tab - memory regions and expected-difference areas "
            "(version, timestamp, CRC).\n\n"
            "4.  The comparison runs automatically. Summary shows the verdict and what changed; "
            "the other tabs show regions, blocks, functions and a memory map.\n\n"
            "5.  Open Hex Comparison to inspect bytes; HTML Report / CSV Export to share results."),
            parent=self.root)

    def shortcuts(self) -> None:
        messagebox.showinfo("Keyboard shortcuts", (
            "Ctrl+1 / Ctrl+2   Open Firmware A / B\n"
            "F5                Compare\n"
            "Ctrl+H            Open hex comparison\n"
            "Ctrl+R            Export HTML report\n"
            "Alt+1 … Alt+7     Switch tabs\n\n"
            "Hex viewer:  n / p next / previous difference,  F3 find,  Ctrl+G go to address"),
            parent=self.root)

    # ---------------------------------------------------------------- status
    def set_status(self, text: str, error: bool = False) -> None:
        self.status.configure(text=("⚠  " if error else "") + text, foreground=theme.C_CHANGED if error else theme.MUTED)

    def _busy(self, busy: bool) -> None:
        self.cancel_btn.configure(state="normal" if busy else "disabled")
        if not busy:
            self.progress.configure(value=0)

    def _on_progress(self, done: int, total: int) -> None:
        if total:
            self.progress.configure(maximum=total, value=done)

    def _run_or_queue(self, action) -> None:
        if self.runner.busy:
            self._queue.append(action)
        else:
            action()

    def _next_queued(self) -> None:
        if self._queue and not self.runner.busy:
            self._queue.pop(0)()

    # ----------------------------------------------------------------- files
    def _panel(self, side: str) -> FilePanel:
        return self.panel_a if side == "A" else self.panel_b

    def _invalidate_session(self) -> None:
        self.session = None
        if self.hex_window is not None and self.hex_window.winfo_exists():
            self.hex_window.set_session(None)
        for view in (self.region_table, self.block_table, self.metadata_view, self.features_view):
            view.show(None)
        self.memory_map.set_session(None)
        self._show_what_changed()
        self.summary.clear("Comparison out of date - waiting for both files.")
        self._refresh_setup_labels()

    def load_file(self, panel: FilePanel, path: str, extra=()) -> None:
        side = panel.side
        if extra and side == "A":  # two files dropped on A: second goes to B
            self._run_or_queue(lambda: self.load_file(self.panel_b, extra[0]))
        if self.runner.busy:
            self._queue.append(lambda: self.load_file(panel, path))
            return
        try:
            fmt, base = panel.options()
        except ValueError as exc:
            messagebox.showerror("Invalid base address", str(exc), parent=self.root)
            return
        self._invalidate_session()
        old = self.images[side]
        self.images[side] = None
        if old is not None:
            old.close()
        panel.set_loading(path)
        self.set_status(f"Loading {os.path.basename(path)}…")
        self._busy(True)
        vaddr = self.elf_vaddr.get()
        sym_path = getattr(self.symbol_tables[side], "path", None)

        def load():
            img = load_firmware(path, fmt, base, vaddr)
            if sym_path:  # symbol file already chosen for this side: use it for identification too
                identify_with_symbols(img, sym_path)
            return img

        def done(img):
            self._busy(False)
            self.images[side] = img
            panel.set_image(img)
            self._refresh_setup_labels()
            self.set_status(f"Loaded {img.name} as Firmware {side}.")
            self.elf_view.show(self.images["A"], self.images["B"], None)
            if self.images["A"] is not None and self.images["B"] is not None and not self._queue:
                self.compare()
            else:
                self._next_queued()

        def failed(exc, tb):
            self._busy(False)
            panel.set_error(path, str(exc))
            self.set_status(f"Failed to load {os.path.basename(path)}.", error=True)
            if not isinstance(exc, FirmwareToolError):
                exc = f"{exc}\n\n{tb}"
            messagebox.showerror("Cannot load firmware", str(exc), parent=self.root)
            self._next_queued()

        self.runner.run(load, done, failed)

    def swap(self) -> None:
        if self.runner.busy:
            return
        pa, pb = self.panel_a.path, self.panel_b.path
        if not pa or not pb:
            return
        self._run_or_queue(lambda: self.load_file(self.panel_a, pb))
        self._queue.append(lambda: self.load_file(self.panel_b, pa))

    def _reload_elf(self) -> None:
        for side in ("A", "B"):
            img = self.images[side]
            if img is not None and img.format == "elf":
                panel = self._panel(side)
                self._run_or_queue(lambda p=panel, path=img.path: self.load_file(p, path))

    # --------------------------------------------------------------- compare
    def compare(self) -> None:
        a, b = self.images["A"], self.images["B"]
        if a is None or b is None:
            self.set_status("Load both Firmware A and Firmware B first.", error=True)
            return
        if self.runner.busy:
            self._queue.append(self.compare)
            return
        cfg = self.config
        errors, _ = cfg.validate()
        if errors:
            messagebox.showerror("Configuration error", "\n".join(errors), parent=self.root)
            return
        self.set_status("Comparing…")
        self._busy(True)

        def done(session):
            self._busy(False)
            self.session = session
            self.update_views()
            self.settings.add_recent(a.path, b.path, getattr(self.symbol_tables["A"], "path", None),
                                     getattr(self.symbol_tables["B"], "path", None), self.config_editor.path)
            self.settings.save()
            if self.nb.select() == str(self.files_tab):  # take the user to the result
                self.nb.select(self.summary_tab)
            self.set_status(f"Compared {a.name} → {b.name}: {session.analysis.verdict} "
                            f"({session.diff.diff_bytes:,} different bytes in {session.diff.elapsed:.2f} s).")
            self._next_queued()

        def failed(exc, tb):
            self._busy(False)
            if isinstance(exc, ComparisonCancelled):
                self.set_status("Comparison cancelled.", error=True)
            else:
                self.set_status("Comparison failed.", error=True)
                msg = str(exc) if isinstance(exc, FirmwareToolError) else f"{exc}\n\n{tb}"
                messagebox.showerror("Comparison failed", msg, parent=self.root)
            self._next_queued()

        self.runner.run(lambda: build_session(a, b, cfg, self.runner.report_progress, self.runner.cancel_event,
                                                      symbols_a=self.symbol_tables["A"], symbols_b=self.symbol_tables["B"]),
                        done, failed, self._on_progress)

    def cancel(self) -> None:
        self._queue.clear()
        self.runner.cancel_event.set()

    def update_views(self) -> None:
        s = self.session
        if s is None:
            return
        self.summary.show(s)
        self.region_table.show(s)
        self.block_table.show(s)
        self.memory_map.set_session(s)
        self.metadata_view.show(s)
        self.features_view.show(s)
        self._show_what_changed()
        self.panel_a.set_image(s.image_a)  # controller identification may have been refined by symbol files
        self.panel_b.set_image(s.image_b)
        self.elf_view.show(s.image_a, s.image_b, s)
        self.checksum_view.refresh_scopes()
        if self.hex_window is not None and self.hex_window.winfo_exists():
            self.hex_window.set_session(s)
        self._refresh_setup_labels()

    def on_config_changed(self, cfg: ProjectConfig, recompare: bool, base_changed: bool) -> None:
        self.config = cfg
        self._refresh_setup_labels()
        if base_changed:
            base = cfg.base_address or 0
            for side in ("A", "B"):
                panel = self._panel(side)
                panel.set_base(base)
                img = self.images[side]
                if img is not None and img.format == "bin":
                    self._run_or_queue(lambda p=panel, path=img.path: self.load_file(p, path))
            if any(self.images[s_] is not None and self.images[s_].format == "bin" for s_ in "AB"):
                return
        if self.session is None:
            if recompare or base_changed:
                self.compare()
            return
        if recompare:
            self.compare()
            return
        try:
            self.session.reanalyze(cfg)
        except FirmwareToolError as exc:
            self.set_status(str(exc), error=True)
            return
        self.update_views()
        self.set_status("Configuration applied - analysis updated.")

    # --------------------------------------------------------------- symbols
    def load_symbols(self, side: str, path: str | None) -> None:
        """Load (or clear, when *path* is None) the ELF / GNU ld map symbols of one build."""
        if path is None:
            self.symbol_tables[side] = None
        else:
            try:
                self.symbol_tables[side] = load_symbol_table(path)
            except FirmwareToolError as exc:
                messagebox.showerror("Cannot load symbols", str(exc), parent=self.root)
                return
            self.set_status(f"Loaded {len(self.symbol_tables[side]):,} symbols for Firmware {side} "
                            f"from {os.path.basename(path)}.")
        img = self.images[side]
        if img is not None and self.session is None:
            identify_with_symbols(img, path)
            self._panel(side).set_image(img)
        if self.session is not None:
            tables = {}
            for s_, img in (("A", self.session.image_a), ("B", self.session.image_b)):
                tables[s_] = self.symbol_tables[s_] or default_symbols(img)
            self.session.set_symbols(tables["A"], tables["B"])
            self.update_views()
        else:
            self.features_view.set_sources(*(t.source if t else None for t in
                                             (self.symbol_tables["A"], self.symbol_tables["B"])))
        self._refresh_setup_labels()

    # ------------------------------------------------------------ hex viewer
    def open_hex(self, addr: int | None = None) -> None:
        if self.session is None:
            self.set_status("Run a comparison first.", error=True)
            return
        if self.hex_window is not None and self.hex_window.winfo_exists():
            if self.hex_window.session is not self.session:
                self.hex_window.set_session(self.session, addr)
            elif addr is not None:
                self.hex_window.set_cursor(addr, center=True)
            self.hex_window.deiconify()
            self.hex_window.lift()
            return
        self.hex_window = HexComparisonWindow(self.root, self.session, addr)

    # --------------------------------------------------------------- exports
    def _default_name(self, ext: str) -> str:
        a, b = self.session.image_a.name, self.session.image_b.name
        return f"compare_{os.path.splitext(a)[0]}_vs_{os.path.splitext(b)[0]}.{ext}"

    def export_html(self) -> None:
        if self.session is None:
            self.set_status("Run a comparison first.", error=True)
            return
        path = filedialog.asksaveasfilename(parent=self.root, title="Save HTML report", defaultextension=".html",
                                            initialdir=self.settings.last_dir,
                                            initialfile=self._default_name("html"),
                                            filetypes=[("HTML report", "*.html")])
        if not path:
            return
        self.settings.remember_file(path)
        from ..reports.html_report import write_html_report
        try:
            write_html_report(self.session, path)
        except OSError as exc:
            messagebox.showerror("Report failed", str(exc), parent=self.root)
            return
        self.set_status(f"Report written: {path}")
        if messagebox.askyesno("Report generated", f"Report written to\n{path}\n\nOpen it in the browser?",
                               parent=self.root):
            webbrowser.open("file:///" + os.path.abspath(path).replace("\\", "/"))

    def export_csv(self) -> None:
        if self.session is None:
            self.set_status("Run a comparison first.", error=True)
            return
        path = filedialog.asksaveasfilename(parent=self.root, title="Export changed blocks", defaultextension=".csv",
                                            initialdir=self.settings.last_dir,
                                            initialfile=self._default_name("csv"),
                                            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        from ..reports.csv_report import write_blocks_csv
        try:
            n = write_blocks_csv(self.session, path)
        except OSError as exc:
            messagebox.showerror("Export failed", str(exc), parent=self.root)
            return
        self.set_status(f"{n:,} changed blocks written to {path}")

    # ----------------------------------------------------------------- misc
    def about(self) -> None:
        messagebox.showinfo("About", f"{APP_NAME} {__version__}\n\nCompares firmware images (.bin, Intel HEX, ELF) "
                                     "by MCU address.\nOriginal files are opened read-only and never modified.",
                            parent=self.root)

    def on_close(self) -> None:
        try:
            if self.root.state() == "normal":
                self.settings.geometry = self.root.geometry()
            self.settings.save()
        except tk.TclError:
            pass
        self.runner.cancel_event.set()
        for img in self.images.values():
            if img is not None:
                img.close()
        self.root.destroy()


def _enable_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # older Windows / not permitted: keep default scaling
        pass


def create_root():
    """Create the Tk root, with native drag-and-drop when tkinterdnd2 is installed."""
    _enable_dpi_awareness()
    try:
        from tkinterdnd2 import DND_FILES, TkinterDnD
        return TkinterDnD.Tk(), DND_FILES
    except Exception:  # not installed or failed to load its Tcl package
        return tk.Tk(), None


def run_gui(file_a: str | None = None, file_b: str | None = None, config_path: str | None = None,
            symbols_a: str | None = None, symbols_b: str | None = None) -> None:
    root, dnd = create_root()
    win = MainWindow(root, dnd)
    for side, path in (("A", symbols_a), ("B", symbols_b)):
        if path:
            win.load_symbols(side, path)
    if config_path:
        root.after(50, lambda: win.config_editor.load(config_path))
    if file_a:
        root.after(100, lambda: win.load_file(win.panel_a, file_a))
    if file_b:
        root.after(150, lambda: win._run_or_queue(lambda: win.load_file(win.panel_b, file_b)))
    root.mainloop()
