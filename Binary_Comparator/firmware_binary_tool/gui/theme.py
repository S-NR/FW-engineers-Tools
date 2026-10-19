"""Visual system: calm light palette, system UI font, soft borders, compact engineering tables.

All colours and fonts used by the GUI live here so the look stays consistent.
"""

from __future__ import annotations

import sys
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

# ------------------------------------------------------------------ palette
BG = "#f4f6f9"  # window surface
PANEL = BG  # kept for compatibility
CARD = "#ffffff"  # fields, tables, drop zones, tiles
FG = "#1f2933"
MUTED = "#66727f"
FAINT = "#9aa5b1"
ACCENT = "#2563eb"
ACCENT_HOVER = "#1d4ed8"
ACCENT_SOFT = "#e8f0fe"
BORDER = "#d8dee6"
BORDER_STRONG = "#c3ccd6"
HEADER_BG = "#1e293b"
HEADER_FG = "#f1f5f9"
HEADER_MUTED = "#94a3b8"
STRIPE = "#f8fafc"
SELECT_BG = "#dbe7fd"

A_COLOR = "#2563eb"  # Firmware A (old)
B_COLOR = "#7c3aed"  # Firmware B (new)

C_SAME = "#15803d"
C_CHANGED = "#c02626"
C_EXPECTED = "#a16207"
C_NP = "#6b7580"

# verdict badge / banner: (foreground, background, icon)
VERDICT_STYLE = {
    "IDENTICAL": ("#166534", "#dcfce7", "✔"),
    "EXPECTED CHANGES ONLY": ("#854d0e", "#fef3c7", "●"),
    "UNEXPECTED CHANGES": ("#991b1b", "#fee2e2", "✖"),
    "NONE": ("#475569", "#e2e8f0", "○"),
    "WARNING": ("#991b1b", "#fee2e2", "⚠"),
}

# hex viewer cell backgrounds
HX_DIFF_BG, HX_DIFF_FG = "#fbd0d0", "#7a0000"
HX_EXP_BG, HX_EXP_FG = "#fde7a9", "#5c3d00"
HX_ONLYA_BG = "#d6e4fd"
HX_ONLYB_BG = "#e9dcfc"
HX_CURSOR_BG = ACCENT
HX_CURROW_BG = "#eef3fd"

MAP_COLORS = {"nodata": "#e5e9ef", "same": "#cfead6", "expected": "#f6c453", "changed": "#dc4545"}

STATUS_COLORS = {
    "SAME": C_SAME, "UNCHANGED": C_SAME, "IDENTICAL": C_SAME,
    "CHANGED": C_CHANGED, "UNEXPECTED": C_CHANGED, "MIXED": C_CHANGED, "UNEXPECTED CHANGES": C_CHANGED,
    "ADDED": C_CHANGED, "REMOVED": C_CHANGED, "ONLY IN A": C_CHANGED, "ONLY IN B": C_CHANGED,
    "EXPECTED": C_EXPECTED, "EXPECTED CHANGE": C_EXPECTED, "CHANGED (EXPECTED)": C_EXPECTED,
    "EXPECTED CHANGES ONLY": C_EXPECTED,
    "NOT PRESENT": C_NP, "NOT FOUND": C_NP,
    "MODIFIED": C_CHANGED, "MOVED": C_NP, "RELOCATED": C_NP, "LIKELY RELOCATED": C_EXPECTED, "NOT IN IMAGE": C_NP,
}

_fonts: dict[str, tkfont.Font] = {}


def mono_font() -> tkfont.Font:
    return _fonts["mono"]


def mono_bold_font() -> tkfont.Font:
    return _fonts["mono_bold"]


def bold_font() -> tkfont.Font:
    return _fonts["bold"]


def big_font() -> tkfont.Font:
    return _fonts["big"]


def title_font() -> tkfont.Font:
    return _fonts["title"]


def kpi_font() -> tkfont.Font:
    return _fonts["kpi"]


def ui_font() -> tkfont.Font:
    return _fonts["ui"]


def small_font() -> tkfont.Font:
    return _fonts["small"]


def _pick(families: set[str], candidates: tuple[str, ...], fallback: str) -> str:
    return next((f for f in candidates if f in families), fallback)


def apply_theme(root: tk.Misc) -> None:
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    families = set(tkfont.families(root))
    ui = _pick(families, ("Segoe UI", "Noto Sans", "Ubuntu", "Cantarell", "DejaVu Sans", "Helvetica Neue",
                          "Helvetica", "Arial"), tkfont.nametofont("TkDefaultFont").cget("family"))
    mono = _pick(families, ("Cascadia Mono", "Consolas", "JetBrains Mono", "DejaVu Sans Mono", "Liberation Mono",
                            "Menlo", "Courier New"), "TkFixedFont")
    size = 10 if sys.platform == "win32" else 10
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont", "TkTooltipFont"):
        try:
            tkfont.nametofont(name).configure(family=ui, size=size)
        except tk.TclError:
            pass
    _fonts["ui"] = tkfont.nametofont("TkDefaultFont")
    _fonts["mono"] = tkfont.Font(root, family=mono, size=size - 1 if mono == "Cascadia Mono" else size)
    _fonts["mono_bold"] = tkfont.Font(root, family=mono, size=_fonts["mono"].cget("size"), weight="bold")
    _fonts["bold"] = tkfont.Font(root, family=ui, size=size, weight="bold")
    _fonts["big"] = tkfont.Font(root, family=ui, size=size + 4, weight="bold")
    _fonts["title"] = tkfont.Font(root, family=ui, size=size + 2, weight="bold")
    _fonts["kpi"] = tkfont.Font(root, family=ui, size=size + 9, weight="bold")
    _fonts["small"] = tkfont.Font(root, family=ui, size=size - 1)
    _fonts["section"] = tkfont.Font(root, family=ui, size=size, weight="bold")

    root.configure(background=BG)
    style.configure(".", background=BG, foreground=FG, bordercolor=BORDER, focuscolor=BG,
                    lightcolor=BORDER, darkcolor=BORDER, troughcolor=BG, font=_fonts["ui"])
    style.configure("TFrame", background=BG)
    style.configure("Panel.TFrame", background=BG)
    style.configure("Card.TFrame", background=CARD)
    style.configure("TLabel", background=BG, foreground=FG)
    style.configure("Panel.TLabel", background=BG)
    style.configure("Card.TLabel", background=CARD)
    style.configure("Muted.TLabel", foreground=MUTED)
    style.configure("Small.TLabel", foreground=MUTED, font=_fonts["small"])
    style.configure("Key.TLabel", foreground=MUTED)
    style.configure("Value.TLabel", font=_fonts["mono"])
    style.configure("Warn.TLabel", foreground=C_CHANGED)
    style.configure("Verdict.TLabel", font=_fonts["big"])
    style.configure("Section.TLabel", foreground=FG, font=_fonts["section"])

    # group boxes: soft border, section-style caption
    style.configure("TLabelframe", background=BG, bordercolor=BORDER, lightcolor=BG, darkcolor=BG,
                    relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label", background=BG, foreground=FG, font=_fonts["section"])
    for name, color in (("A", A_COLOR), ("B", B_COLOR)):
        style.configure(f"{name}.TLabelframe", background=BG, bordercolor=color, lightcolor=BG, darkcolor=BG,
                        relief="solid", borderwidth=1)
        style.configure(f"{name}.TLabelframe.Label", background=BG, foreground=color, font=_fonts["title"])

    # buttons
    style.configure("TButton", padding=(12, 5), background=CARD, foreground=FG, bordercolor=BORDER_STRONG,
                    lightcolor=CARD, darkcolor=CARD, relief="flat", anchor="center")
    style.map("TButton",
              background=[("disabled", BG), ("pressed", "#dfe8f8"), ("active", "#eef3fc")],
              foreground=[("disabled", FAINT)],
              bordercolor=[("active", ACCENT), ("focus", ACCENT)],
              lightcolor=[("pressed", "#dfe8f8"), ("active", "#eef3fc")],
              darkcolor=[("pressed", "#dfe8f8"), ("active", "#eef3fc")])
    style.configure("Accent.TButton", padding=(16, 6), background=ACCENT, foreground="#ffffff",
                    bordercolor=ACCENT, lightcolor=ACCENT, darkcolor=ACCENT, font=_fonts["bold"])
    style.map("Accent.TButton",
              background=[("disabled", BORDER), ("pressed", "#1e40af"), ("active", ACCENT_HOVER)],
              lightcolor=[("pressed", "#1e40af"), ("active", ACCENT_HOVER)],
              darkcolor=[("pressed", "#1e40af"), ("active", ACCENT_HOVER)],
              bordercolor=[("active", ACCENT_HOVER)], foreground=[("disabled", "#ffffff")])
    style.configure("Tool.TButton", padding=(8, 3))
    style.configure("Link.TButton", padding=(4, 2), background=BG, foreground=ACCENT, bordercolor=BG,
                    lightcolor=BG, darkcolor=BG, relief="flat")
    style.map("Link.TButton", foreground=[("active", ACCENT_HOVER)], background=[("active", BG)],
              bordercolor=[("active", BG), ("focus", BG)])

    # inputs
    for widget in ("TEntry", "TCombobox"):
        style.configure(widget, fieldbackground=CARD, background=CARD, bordercolor=BORDER_STRONG,
                        lightcolor=CARD, darkcolor=CARD, padding=(6, 3), insertcolor=FG,
                        arrowcolor=MUTED, selectbackground=SELECT_BG, selectforeground=FG)
        style.map(widget, bordercolor=[("focus", ACCENT)], lightcolor=[("focus", ACCENT_SOFT)],
                  fieldbackground=[("readonly", CARD), ("disabled", BG)])
    style.configure("Value.TEntry", fieldbackground=CARD, bordercolor=BORDER, lightcolor=CARD, darkcolor=CARD,
                    padding=(6, 2))
    style.map("Value.TEntry", bordercolor=[("focus", ACCENT)], fieldbackground=[("readonly", CARD)])
    style.configure("TCheckbutton", background=BG, focuscolor=BG)
    style.configure("TRadiobutton", background=BG, focuscolor=BG)

    # tables
    row_h = int(_fonts["ui"].metrics("linespace") * 1.7)
    style.configure("Treeview", rowheight=row_h, background=CARD, fieldbackground=CARD, foreground=FG,
                    bordercolor=BORDER, lightcolor=CARD, darkcolor=CARD, borderwidth=0)
    style.configure("Treeview.Heading", font=_fonts["bold"], background="#eef1f5", foreground="#334155",
                    relief="flat", bordercolor=BORDER, lightcolor="#eef1f5", darkcolor="#eef1f5", padding=(6, 5))
    style.map("Treeview.Heading", background=[("active", "#e3e8ef")])
    style.map("Treeview", background=[("selected", SELECT_BG)], foreground=[("selected", FG)])

    # tabs
    style.configure("TNotebook", background=BG, bordercolor=BORDER, lightcolor=BG, darkcolor=BG,
                    tabmargins=(8, 6, 8, 0))
    style.configure("TNotebook.Tab", padding=(14, 7), background="#e8ecf1", foreground=MUTED,
                    bordercolor=BORDER, lightcolor="#e8ecf1", darkcolor="#e8ecf1", font=_fonts["ui"])
    style.map("TNotebook.Tab",
              background=[("selected", BG), ("active", "#f0f3f7")],
              foreground=[("selected", ACCENT), ("active", FG)],
              lightcolor=[("selected", BG)], darkcolor=[("selected", BG)],
              font=[("selected", _fonts["bold"])])

    # misc
    style.configure("Horizontal.TProgressbar", troughcolor="#e5e9ef", background=ACCENT, bordercolor=BG,
                    lightcolor=ACCENT, darkcolor=ACCENT, thickness=8)
    for orient in ("Vertical", "Horizontal"):
        style.configure(f"{orient}.TScrollbar", background="#d5dbe3", troughcolor=BG, bordercolor=BG,
                        lightcolor="#d5dbe3", darkcolor="#d5dbe3", arrowcolor=MUTED, arrowsize=13)
        style.map(f"{orient}.TScrollbar", background=[("active", "#bcc5d0")])
    style.configure("TSeparator", background=BORDER)
    style.configure("TPanedwindow", background=BG)
    style.configure("Sash", sashthickness=6, background=BG)
    style.configure("Bottom.TFrame", background="#eaeef3")
    style.configure("Bottom.TLabel", background="#eaeef3", foreground=MUTED)
    root.option_add("*Menu.font", _fonts["ui"])
    root.option_add("*TCombobox*Listbox.font", _fonts["ui"])
    root.option_add("*TCombobox*Listbox.selectBackground", SELECT_BG)
    root.option_add("*TCombobox*Listbox.selectForeground", FG)


def tag_status_colors(tree: ttk.Treeview) -> None:
    """Configure Treeview tags named after statuses so rows can be coloured, plus zebra stripes."""
    for status, color in STATUS_COLORS.items():
        tree.tag_configure(status, foreground=color)
    tree.tag_configure("odd", background=STRIPE)


def app_icon(root: tk.Misc) -> tk.PhotoImage:
    """Small generated window icon: two firmware 'memory maps' with a highlighted difference."""
    size = 32
    img = tk.PhotoImage(master=root, width=size, height=size)
    img.put(HEADER_BG, to=(0, 0, size, size))
    for row in range(4):
        for col in range(4):
            x0, y0 = 4 + col * 6, 4 + row * 6
            color = "#dc4545" if (row, col) in ((1, 2), (2, 1)) else ("#60a5fa" if col < 2 else "#a78bfa")
            img.put(color, to=(x0, y0, x0 + 5, y0 + 5))
    return img
