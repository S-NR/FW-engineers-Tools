"""TX / RX Analyzer tab: decode a request/response pair and highlight differences."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QGridLayout, QHBoxLayout, QHeaderView, QLabel, QPlainTextEdit, QPushButton,
                               QSplitter, QVBoxLayout, QWidget)

from core.comparison import byte_diff, compare_fields, text_fields
from core.decoder import DecodeResult, decode_packet
from core.protocol import ProtocolDefinition
from gui import theme
from gui.widgets.common import CopyableTable, SectionHeader, StatusIndicator, make_item
from gui.widgets.hex_input import HexInputEdit
from gui.widgets.hex_view import HexAsciiView
from utils.hex_utils import try_parse_hex


class TxRxAnalyzerView(QWidget):
    packetDecoded = Signal(str, object)  # direction, DecodeResult - for history

    def __init__(self, protocol_provider: Callable[[], ProtocolDefinition | None], font_size: int = 11,
                 parent=None) -> None:
        super().__init__(parent)
        self.protocol_provider = protocol_provider
        self.results: dict[str, DecodeResult | None] = {"TX": None, "RX": None}

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        inputs = QGridLayout()
        self.inputs: dict[str, HexInputEdit] = {}
        self.status: dict[str, StatusIndicator] = {}
        for row, direction in enumerate(("TX", "RX")):
            label = SectionHeader(direction)
            edit = HexInputEdit(font_size=font_size, placeholder=f"{direction} packet HEX")
            edit.setMaximumHeight(64)
            edit.decodeRequested.connect(self.analyze)
            status = StatusIndicator()
            inputs.addWidget(label, row, 0, Qt.AlignmentFlag.AlignTop)
            inputs.addWidget(edit, row, 1)
            inputs.addWidget(status, row, 2, Qt.AlignmentFlag.AlignTop)
            self.inputs[direction] = edit
            self.status[direction] = status
        inputs.setColumnStretch(1, 1)
        root.addLayout(inputs)

        buttons = QHBoxLayout()
        analyze = QPushButton("Decode && Compare")
        analyze.setObjectName("Primary")
        analyze.setToolTip("Decode both packets with the selected protocol (Ctrl+Enter)")
        analyze.clicked.connect(self.analyze)
        swap = QPushButton("Swap")
        swap.setToolTip("Swap TX and RX")
        swap.clicked.connect(self.swap)
        clear = QPushButton("Clear")
        clear.clicked.connect(self.clear)
        for b in (analyze, swap, clear):
            buttons.addWidget(b)
        buttons.addStretch()
        root.addLayout(buttons)

        splitter = QSplitter(Qt.Orientation.Vertical)

        top = QWidget()
        tl = QHBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        summary_box = QWidget()
        sl = QVBoxLayout(summary_box)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.addWidget(SectionHeader("Summary"))
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setFont(theme.mono_font(font_size))
        sl.addWidget(self.summary)
        tl.addWidget(summary_box, 2)

        fields_box = QWidget()
        fl = QVBoxLayout(fields_box)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.addWidget(SectionHeader("Field comparison"))
        self.field_table = CopyableTable(0, 4)
        self.field_table.setHorizontalHeaderLabels(["Field", "TX", "RX", "Same"])
        self.field_table.setFont(theme.mono_font(font_size))
        self.field_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.field_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        fl.addWidget(self.field_table)
        tl.addWidget(fields_box, 3)
        splitter.addWidget(top)

        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.addWidget(SectionHeader("Byte differences (positional, highlighted)"))
        hexes = QHBoxLayout()
        self.hex_views: dict[str, HexAsciiView] = {}
        for direction in ("TX", "RX"):
            box = QVBoxLayout()
            box.addWidget(QLabel(direction))
            view = HexAsciiView(font_size=font_size)
            box.addWidget(view)
            hexes.addLayout(box)
            self.hex_views[direction] = view
        bl.addLayout(hexes)
        splitter.addWidget(bottom)
        root.addWidget(splitter, 1)

    # ---------------------------------------------------------------- API
    def set_packets(self, tx: str, rx: str) -> None:
        self.inputs["TX"].setPlainText(tx)
        self.inputs["RX"].setPlainText(rx)

    def swap(self) -> None:
        tx, rx = self.inputs["TX"].toPlainText(), self.inputs["RX"].toPlainText()
        self.set_packets(rx, tx)

    def clear(self) -> None:
        for d in ("TX", "RX"):
            self.inputs[d].clear()
            self.hex_views[d].clear_data()
            self.status[d].set_status("idle", "Ready")
        self.summary.clear()
        self.field_table.setRowCount(0)

    def refresh_theme(self) -> None:
        for s in self.status.values():
            s.refresh_theme()
        if self.results["TX"] or self.results["RX"]:
            self._show_diff()

    def analyze(self) -> None:
        protocol = self.protocol_provider()
        lines: list[str] = []
        for d in ("TX", "RX"):
            text = self.inputs[d].toPlainText()
            self.results[d] = None
            if not text.strip():
                self.status[d].set_status("idle", "empty")
                continue
            data, err = try_parse_hex(text)
            if data is None:
                self.status[d].set_status("error", "Invalid HEX data", err or "")
                lines += [d, f"  Invalid HEX data: {err}", ""]
                continue
            result = decode_packet(protocol, data)
            self.results[d] = result
            level = "ok" if result.ok and not result.warnings else ("warn" if result.ok else "error")
            self.status[d].set_status(level, result.status, result.status_message())
            self.packetDecoded.emit(d, result)

            lines.append(d)
            texts = text_fields(result)
            label = "Command" if d == "TX" else "Response"
            if texts:
                for name, value in texts:
                    lines.append(f"  {label} ({name}): \"{value}\"")
            else:
                lines.append(f"  {label}: (no text fields in this protocol)")
            for c in result.checks:
                lines.append(f"  {c.kind} {c.algorithm}: {'VALID' if c.valid else 'INVALID'}")
            for e in result.errors:
                lines.append(f"  ✗ {e}")
            for w in result.warnings:
                lines.append(f"  ⚠ {w}")
            lines.append("")

        tx, rx = self.results["TX"], self.results["RX"]
        if tx and rx:
            diffs = [b for b in byte_diff(tx.data, rx.data) if not b.same]
            lines.append(f"Lengths: TX {len(tx.data)} bytes, RX {len(rx.data)} bytes; "
                         f"{len(diffs)} byte position(s) differ.")
            changed = [cmp.name for cmp in compare_fields(tx, rx) if not cmp.same]
            lines.append(f"Fields that differ: {', '.join(changed)}" if changed else "All fields identical.")
        self.summary.setPlainText("\n".join(lines).rstrip())
        self._show_fields()
        self._show_diff()

    def _show_fields(self) -> None:
        tx, rx = self.results["TX"], self.results["RX"]
        self.field_table.setRowCount(0)
        if not (tx or rx):
            return
        empty = DecodeResult("", b"")
        c = theme.colors()
        for cmp in compare_fields(tx or empty, rx or empty):
            row = self.field_table.rowCount()
            self.field_table.insertRow(row)
            same_text = "=" if cmp.same else "≠"
            cells = (cmp.name, cmp.a_display if cmp.a_display is not None else "—",
                     cmp.b_display if cmp.b_display is not None else "—", same_text)
            for col, text in enumerate(cells):
                item = make_item(text, align_center=col == 3)
                if not cmp.same and col in (1, 2, 3):
                    item.setBackground(QBrush(QColor(c.diff)))
                self.field_table.setItem(row, col, item)

    def _show_diff(self) -> None:
        tx, rx = self.results["TX"], self.results["RX"]
        if tx and rx:
            diff = {b.offset for b in byte_diff(tx.data, rx.data) if not b.same}
            self.hex_views["TX"].set_data(tx.data, diff_offsets={o for o in diff if o < len(tx.data)})
            self.hex_views["RX"].set_data(rx.data, diff_offsets={o for o in diff if o < len(rx.data)})
        else:
            for d in ("TX", "RX"):
                r = self.results[d]
                if r:
                    self.hex_views[d].set_data(r.data)
                else:
                    self.hex_views[d].clear_data()
