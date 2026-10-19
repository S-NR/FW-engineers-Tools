"""Firmware A / Firmware B selection card."""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, ttk

from ..core.formatting import addr_width, fmt_addr, parse_int
from . import theme
from .settings import get_settings
from .widgets import DropZone, KeyValueGrid, tip

FILETYPES = [("Firmware images", "*.bin *.hex *.ihex *.ihx *.elf *.axf *.out"), ("Raw binary", "*.bin"),
             ("Intel HEX", "*.hex *.ihex *.ihx"), ("ELF", "*.elf *.axf *.out"), ("All files", "*.*")]
FORMAT_CHOICES = {"Auto-detect": "auto", "Raw BIN": "bin", "Intel HEX": "hex", "ELF": "elf"}
INFO_KEYS = ["File", "Size", "SHA-256", "Format", "Address range", "Data", "Controller"]


class FilePanel(ttk.LabelFrame):
    def __init__(self, master, title: str, on_load, dnd_files=None, side: str = "A"):
        super().__init__(master, text=f"  {title}  ", padding=(12, 8), style=f"{side}.TLabelframe")
        self.on_load = on_load
        self.side = side
        self.path: str | None = None
        accent = theme.A_COLOR if side == "A" else theme.B_COLOR

        if dnd_files:
            hint, subhint = "Drop a firmware file here", "or click to browse   ·   .bin   .hex   .elf"
        else:
            hint, subhint = "Click to choose a firmware file", ".bin   ·   .hex   ·   .elf"
        self.drop = DropZone(self, self.browse, accent, hint, subhint=subhint)
        self.drop.pack(fill="x")
        tip(self.drop, "Click to browse for a .bin, Intel .hex or .elf file"
                       + (", or drag a file from Explorer onto this area." if dnd_files else
                          ". Install 'tkinterdnd2' to enable drag-and-drop."))

        opts = ttk.Frame(self)
        opts.pack(fill="x", pady=(8, 4))
        ttk.Label(opts, text="Format", style="Muted.TLabel").pack(side="left")
        self.fmt = ttk.Combobox(opts, values=list(FORMAT_CHOICES), state="readonly", width=12)
        self.fmt.set("Auto-detect")
        self.fmt.pack(side="left", padx=(6, 14))
        tip(self.fmt, "Auto-detect recognises ELF by its header and Intel HEX by its text records. "
                      "Everything else is treated as a raw binary.")
        ttk.Label(opts, text="Base address (.bin)", style="Muted.TLabel").pack(side="left")
        self.base_var = tk.StringVar(value="0x00000000")
        be = ttk.Entry(opts, textvariable=self.base_var, width=12, font=theme.mono_font())
        be.pack(side="left", padx=6)
        be.bind("<Return>", lambda e: self.reload())
        tip(be, "MCU address of the first byte of a raw .bin file (e.g. 0x08000000 for STM32 flash). "
                "HEX and ELF files carry their own addresses. Press Enter to reload.")
        tip(ttk.Button(opts, text="Reload", command=self.reload), "Load the file again with the current "
                                                                  "format / base address").pack(side="right")

        self.info = KeyValueGrid(self, INFO_KEYS, value_width=40)
        self.info.pack(fill="x", pady=(4, 0))
        self.warn = ttk.Label(self, style="Warn.TLabel", wraplength=620, justify="left")
        self.warn.pack(fill="x", pady=(4, 0))

        if dnd_files:
            for w in (self, self.drop):
                w.drop_target_register(dnd_files)
                w.dnd_bind("<<Drop>>", self._on_drop)

    # ---------------------------------------------------------------- input
    def _on_drop(self, event):
        paths = list(self.tk.splitlist(event.data))
        if paths:
            self.on_load(self, paths[0], extra=paths[1:])
        return event.action

    def browse(self) -> None:
        settings = get_settings()
        path = filedialog.askopenfilename(parent=self, title=f"Open Firmware {self.side}",
                                          filetypes=FILETYPES, initialdir=settings.last_dir)
        if path:
            settings.remember_file(path)
            self.on_load(self, path)

    def reload(self) -> None:
        if self.path:
            self.on_load(self, self.path)

    def options(self) -> tuple[str, int]:
        """(format, base address); raises ValueError for an invalid base."""
        return FORMAT_CHOICES[self.fmt.get()], parse_int(self.base_var.get())

    def set_base(self, base: int) -> None:
        self.base_var.set(f"0x{base:08X}")

    # -------------------------------------------------------------- display
    def set_loading(self, path: str) -> None:
        self.path = path
        self.drop.set_file(os.path.basename(path) + "  — loading…", path)
        self.info.clear()
        self.info.set("File", os.path.basename(path))
        self.info.set("Size", "loading…")
        self.warn.configure(text="")

    def set_error(self, path: str, message: str) -> None:
        self.path = path
        self.drop.set_file(os.path.basename(path) + "  — could not be loaded", path, error=True)
        self.info.clear()
        self.info.set("File", os.path.basename(path))
        self.warn.configure(text=f"Error: {message}", foreground=theme.C_CHANGED)

    def set_image(self, img) -> None:
        self.path = img.path
        self.drop.set_file(img.name, img.path)
        rng = img.address_range
        w = addr_width(rng[1] if rng else None)
        self.info.set("File", img.name)
        self.info.set("Size", f"{img.file_size:,} bytes")
        self.info.set("SHA-256", img.sha256)
        fmt = img.format.upper()
        if img.format == "bin":
            fmt += f"  (base {fmt_addr(img.base_address, w)})"
        elif img.format == "elf" and img.elf is not None:
            fmt += f"  ({img.elf.machine}, ELF{img.elf.elf_class}, {img.info.get('Address mode', '')})"
        self.info.set("Format", fmt)
        self.info.set("Address range", f"{fmt_addr(rng[0], w)} - {fmt_addr(rng[1], w)}" if rng else "(no data)")
        self.info.set("Data", f"{img.data_size:,} bytes in {len(img.segments)} segment(s)")
        self.info.set("Controller", img.mcu.short() if img.mcu is not None else "unknown")
        self.warn.configure(text="\n".join(f"Note: {w_}" for w_ in img.warnings), foreground=theme.C_EXPECTED)
