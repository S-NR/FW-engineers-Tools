"""Reusable Tk helpers: sortable tables, key/value grids, tooltips, drop zone, background tasks."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
import traceback
from tkinter import ttk
from typing import Any, Callable

from . import theme


class DataTable(ttk.Frame):
    """Treeview with scrollbars, click-to-sort headings and a row-limit notice."""

    def __init__(self, master, columns: list[tuple[str, str, int, str]], height: int = 10,
                 row_limit: int | None = None, selectmode: str = "browse",
                 empty_text: str = "Nothing to show yet - load Firmware A and B on the Files tab."):
        """columns: (id, heading, width, anchor 'w'|'e'|'center')."""
        super().__init__(master)
        self.columns = columns
        self.row_limit = row_limit
        self.empty_text = empty_text
        self._sort_state: tuple[str, bool] | None = None
        self._rows: list[tuple[str, tuple, tuple, Any]] = []  # (iid, values, tags, sortkeys)
        ids = [c[0] for c in columns]
        self.tree = ttk.Treeview(self, columns=ids, show="headings", height=height, selectmode=selectmode)
        for cid, heading, width, anchor in columns:
            self.tree.heading(cid, text=heading, command=lambda c=cid: self.sort_by(c))
            width = max(width, theme.bold_font().measure(heading) + 30)  # never truncate a heading
            self.tree.column(cid, width=width, minwidth=40, anchor=anchor, stretch=anchor == "w")
        vs = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        hs = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        self.notice = ttk.Label(self, style="Muted.TLabel")
        self.notice.grid(row=2, column=0, columnspan=2, sticky="w")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        theme.tag_status_colors(self.tree)
        self.tree.configure(style="Treeview")
        self._mono_cols = set()

    def set_rows(self, rows: list[tuple[str, tuple, tuple, tuple]], total: int | None = None) -> None:
        """rows: (iid, display values, tags, sort keys aligned with columns)."""
        self._rows = rows
        self._render(total)

    def _render(self, total: int | None = None) -> None:
        self.tree.delete(*self.tree.get_children())
        rows = self._rows
        if self._sort_state:
            col, rev = self._sort_state
            idx = [c[0] for c in self.columns].index(col)
            rows = sorted(rows, key=lambda r: _safe_key(r[3][idx]), reverse=rev)
        limit = self.row_limit or len(rows)
        for n, (iid, values, tags, _) in enumerate(rows[:limit]):
            self.tree.insert("", "end", iid=iid, values=values, tags=tuple(tags) + (("odd",) if n % 2 else ()))
        n = total if total is not None else len(rows)
        if len(rows) > limit:
            self.notice.configure(text=f"Showing {limit:,} of {n:,} rows - refine the filter or export CSV "
                                       f"for the complete list.")
        else:
            self.notice.configure(text=f"{len(rows):,} rows" if rows else self.empty_text)

    def sort_by(self, col: str) -> None:
        rev = bool(self._sort_state and self._sort_state[0] == col and not self._sort_state[1])
        self._sort_state = (col, rev)
        for cid, heading, _, _ in self.columns:
            arrow = (" ▼" if rev else " ▲") if cid == col else ""
            self.tree.heading(cid, text=heading + arrow)
        self._render()

    def selected_iid(self) -> str | None:
        sel = self.tree.selection()
        return sel[0] if sel else None

    def clear(self) -> None:
        self.set_rows([])


def _safe_key(k):
    # mixed None/str/int sort keys: None sorts last
    if k is None:
        return (2, 0)
    if isinstance(k, (int, float)):
        return (0, k)
    return (1, str(k).lower())


class KeyValueGrid(ttk.Frame):
    """Two-column grid of 'Key: value' labels with selectable values."""

    def __init__(self, master, keys: list[str], columns: int = 1, value_width: int = 34, style: str = ""):
        super().__init__(master, style=style)
        self.vars: dict[str, tk.StringVar] = {}
        self.entries: dict[str, ttk.Entry] = {}
        per_col = -(-len(keys) // columns)
        for i, key in enumerate(keys):
            col, row = divmod(i, per_col)
            ttk.Label(self, text=key, style="Key.TLabel" if not style else "Panel.TLabel").grid(
                row=row, column=col * 2, sticky="w", padx=(16 if col else 0, 10), pady=2)
            var = tk.StringVar(value="-")
            ent = ttk.Entry(self, textvariable=var, state="readonly", width=value_width, font=theme.mono_font(),
                            style="Value.TEntry", takefocus=0)
            ent.grid(row=row, column=col * 2 + 1, sticky="we", pady=2)
            self.vars[key] = var
            self.entries[key] = ent
            self.columnconfigure(col * 2 + 1, weight=1)

    def set(self, key: str, value: str) -> None:
        self.vars[key].set(value)

    def clear(self) -> None:
        for v in self.vars.values():
            v.set("-")


class TaskRunner:
    """Run a function on a worker thread; deliver result/error on the Tk thread."""

    def __init__(self, root: tk.Misc):
        self.root = root
        self.busy = False
        self.cancel_event = threading.Event()
        self._q: queue.Queue = queue.Queue()
        self.progress = (0, 0)

    def run(self, func: Callable[[], Any], on_done: Callable[[Any], None],
            on_error: Callable[[BaseException, str], None], on_progress: Callable[[int, int], None] | None = None) -> None:
        self.busy = True
        self.cancel_event.clear()
        self.progress = (0, 0)

        def worker():
            try:
                self._q.put(("ok", func()))
            except BaseException as exc:  # delivered to the UI thread
                self._q.put(("err", (exc, traceback.format_exc())))

        threading.Thread(target=worker, daemon=True).start()
        self._poll(on_done, on_error, on_progress)

    def report_progress(self, done: int, total: int) -> None:
        self.progress = (done, total)  # called from worker; read by poller

    def _poll(self, on_done, on_error, on_progress) -> None:
        try:
            kind, payload = self._q.get_nowait()
        except queue.Empty:
            if on_progress:
                on_progress(*self.progress)
            self.root.after(40, self._poll, on_done, on_error, on_progress)
            return
        self.busy = False
        if kind == "ok":
            on_done(payload)
        else:
            on_error(*payload)


class Tooltip:
    """Small hover help for any widget."""

    def __init__(self, widget: tk.Misc, text: str, delay: int = 500):
        self.widget, self.text, self.delay = widget, text, delay
        self._after = None
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None) -> None:
        self._cancel()
        self._after = self.widget.after(self.delay, self._show)

    def _cancel(self) -> None:
        if self._after is not None:
            self.widget.after_cancel(self._after)
            self._after = None

    def _show(self) -> None:
        if self._tip is not None or not self.text:
            return
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x}+{y}")
        tk.Label(tip, text=self.text, justify="left", background="#1e293b", foreground="#f1f5f9",
                 font=theme.small_font(), padx=8, pady=5, wraplength=380).pack()
        self._tip = tip

    def _hide(self, _e=None) -> None:
        self._cancel()
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


def tip(widget: tk.Misc, text: str) -> tk.Misc:
    """Attach a tooltip and return the widget (for chaining in layout code)."""
    Tooltip(widget, text)
    return widget


class DropZone(tk.Canvas):
    """Dashed drop area: shows a hint when empty, the file name and folder once a file is loaded.

    Clicking anywhere calls *on_click* (browse). Long paths are shortened in the middle to fit.
    """

    def __init__(self, master, on_click, accent: str, hint: str, height: int = 74, subhint: str = ""):
        super().__init__(master, height=height, background=theme.CARD, highlightthickness=0, cursor="hand2")
        self.on_click, self.accent, self.hint, self.subhint = on_click, accent, hint, subhint
        self.path: str | None = None
        self.name: str | None = None
        self.error = False
        self._hover = False
        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<Button-1>", lambda e: self.on_click())

    def _set_hover(self, on: bool) -> None:
        self._hover = on
        self.redraw()

    def set_file(self, name: str | None, path: str | None, error: bool = False) -> None:
        self.name, self.path, self.error = name, path, error
        self.redraw()

    def _shorten(self, text: str, font, avail: int) -> str:
        if font.measure(text) <= avail:
            return text
        head, tail = text[:3], text[3:]
        while tail and font.measure(head + "…" + tail) > avail:
            tail = tail[1:]
        return head + "…" + tail

    def redraw(self) -> None:
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 20:
            return
        outline = self.accent if self._hover else (theme.C_CHANGED if self.error else theme.BORDER_STRONG)
        fill = theme.ACCENT_SOFT if self._hover else theme.CARD
        self.create_rectangle(2, 2, w - 3, h - 3, outline=outline, dash=(5, 3) if not self.name else (),
                              width=1, fill=fill)
        if not self.name:
            self.create_text(w // 2, h // 2 - 9, text="↓   " + self.hint, fill=theme.FG, font=theme.bold_font())
            self.create_text(w // 2, h // 2 + 12, text=self.subhint, fill=theme.MUTED, font=theme.small_font())
            return
        # loaded file: colour bar, name, folder, "Change" hint
        self.create_rectangle(2, 2, 8, h - 3, outline="", fill=theme.C_CHANGED if self.error else self.accent)
        change = "Change…" if not self.error else "Choose another file…"
        cw = theme.small_font().measure(change) + 24
        avail = max(40, w - 34 - cw)
        self.create_text(20, h // 2 - 10, anchor="w", text=self._shorten(self.name, theme.title_font(), avail),
                         fill=theme.C_CHANGED if self.error else theme.FG, font=theme.title_font())
        self.create_text(20, h // 2 + 12, anchor="w", text=self._shorten(self.path or "", theme.small_font(), avail),
                         fill=theme.MUTED, font=theme.small_font())
        self.create_text(w - 14, h // 2, anchor="e", text=change, fill=self.accent, font=theme.small_font())
