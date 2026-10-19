"""Read-only HEX / ASCII side-by-side view with per-byte colouring."""
from __future__ import annotations

import html

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QTextEdit

from gui import theme
from utils.ascii_utils import is_printable


class HexAsciiView(QTextEdit):
    def __init__(self, parent=None, font_size: int = 11, bytes_per_row: int = 16) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.setFont(theme.mono_font(font_size))
        self.bytes_per_row = bytes_per_row
        self._data = b""
        self._colors: dict[int, QColor] = {}
        self._diff: set[int] = set()
        self._selected: tuple[int, int] | None = None
        self.setPlaceholderText("HEX / ASCII view")

    def set_data(self, data: bytes, regions: list[tuple[int, int, QColor]] | None = None,
                 diff_offsets: set[int] | None = None) -> None:
        self._data = bytes(data)
        self._colors = {}
        for start, length, color in regions or []:
            for i in range(start, start + length):
                self._colors[i] = color
        self._diff = set(diff_offsets or ())
        self._selected = None
        self._render()

    def select_range(self, start: int, length: int) -> None:
        self._selected = (start, start + length) if length > 0 else None
        self._render()

    def clear_data(self) -> None:
        self._data = b""
        self.clear()

    def refresh_theme(self) -> None:
        self._render()

    def _style(self, i: int) -> str:
        c = theme.colors()
        styles = []
        if i in self._diff:
            styles.append(f"background:{c.diff}")
        elif i in self._colors:
            styles.append(f"background:{self._colors[i].name()}")
        if self._selected and self._selected[0] <= i < self._selected[1]:
            styles.append(f"color:#ffffff;background:{c.accent}")
        return ";".join(styles)

    def _render(self) -> None:
        if not self._data:
            self.clear()
            return
        c = theme.colors()
        w = self.bytes_per_row
        rows = []
        header = "Offset  " + " ".join(f"{i:02X}" for i in range(w)) + "   ASCII"
        rows.append(f'<span style="color:{c.muted}">{html.escape(header)}</span>')
        for row_start in range(0, len(self._data), w):
            chunk = self._data[row_start:row_start + w]
            hex_parts, ascii_parts = [], []
            for j, b in enumerate(chunk):
                i = row_start + j
                style = self._style(i)
                ch = html.escape(chr(b)) if is_printable(b) else "."
                hex_parts.append(f'<span style="{style}">{b:02X}</span>' if style else f"{b:02X}")
                ascii_parts.append(f'<span style="{style}">{ch}</span>' if style else ch)
            pad = "   " * (w - len(chunk))
            rows.append(f'<span style="color:{c.muted}">{row_start:06X}</span>  '
                        + " ".join(hex_parts) + pad + "   " + "".join(ascii_parts))
        family = self.font().family()
        self.setHtml(f'<pre style="font-family:\'{family}\'; margin:0">' + "\n".join(rows) + "</pre>")
