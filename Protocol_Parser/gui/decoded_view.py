"""Decoded packet view: layout boxes, field table, HEX/ASCII, checks, messages, analysis."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QGuiApplication
from PySide6.QtWidgets import (QHBoxLayout, QHeaderView, QPlainTextEdit, QPushButton, QScrollArea, QSplitter,
                               QTabWidget, QVBoxLayout, QWidget)

from core.decoder import DecodeResult
from gui import theme
from gui.widgets.common import CopyableTable, SectionHeader, make_item
from gui.widgets.hex_view import HexAsciiView
from gui.widgets.packet_layout import LayoutBox, PacketLayoutWidget
from utils.hex_utils import format_hex

COLUMNS = ["Offset", "Bytes", "Field", "Type", "Value", "Interpretation", "Status"]
STATUS_TEXT = {"ok": "✓", "warning": "⚠", "error": "✗", "undefined": "?"}


class DecodedView(QWidget):
    analyzeRequested = Signal()

    def __init__(self, font_size: int = 11, bytes_per_row: int = 16, parent=None) -> None:
        super().__init__(parent)
        self.result: DecodeResult | None = None
        self._row_field: list[int] = []  # table row -> index in result.fields (-1 for bit rows)
        self._row_bit_parent: list[int] = []
        self.font_size = font_size

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 6)

        header = QHBoxLayout()
        header.addWidget(SectionHeader("Decoded packet"))
        header.addStretch()
        self.copy_table_btn = QPushButton("Copy Table")
        self.copy_table_btn.setToolTip("Copy the decoded table as tab-separated text (Ctrl+C copies selected rows)")
        self.copy_report_btn = QPushButton("Copy Report")
        self.copy_report_btn.setToolTip("Copy a plain-text decode report")
        self.ai_btn = QPushButton("Analyze Packet with AI")
        self.ai_btn.setToolTip("Optional: produce a structured explanation. The decode table stays the source of truth.")
        self.copy_table_btn.clicked.connect(lambda: self.table.copy_all())
        self.copy_report_btn.clicked.connect(self.copy_report)
        self.ai_btn.clicked.connect(self.analyzeRequested)
        for b in (self.copy_table_btn, self.copy_report_btn, self.ai_btn):
            header.addWidget(b)
        layout.addLayout(header)

        self.layout_widget = PacketLayoutWidget(font_size=max(8, font_size - 1))
        self.layout_widget.fieldClicked.connect(self._select_row)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.layout_widget)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFixedHeight(self.layout_widget.height() + 16)  # room for a horizontal scrollbar
        layout.addWidget(scroll)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.table = CopyableTable(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setFont(theme.mono_font(font_size))
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        for col, width in enumerate((64, 150, 140, 80, 160, 200, 50)):
            self.table.setColumnWidth(col, width)
        self.table.itemSelectionChanged.connect(self._on_table_selection)
        splitter.addWidget(self.table)

        self.side_tabs = QTabWidget()
        self.hex_view = HexAsciiView(font_size=font_size, bytes_per_row=bytes_per_row)
        self.checks_view = self._text_panel("CRC / checksum results appear here")
        self.messages_view = self._text_panel("Errors and warnings appear here")
        self.analysis_view = self._text_panel("Click 'Analyze Packet with AI' for a structured explanation.\n"
                                              "Inferences are always labelled; the decode table is authoritative.")
        self.side_tabs.addTab(self.hex_view, "HEX / ASCII")
        self.side_tabs.addTab(self.checks_view, "Checks")
        self.side_tabs.addTab(self.messages_view, "Messages")
        self.side_tabs.addTab(self.analysis_view, "Analysis")
        splitter.addWidget(self.side_tabs)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([1000, 420])
        layout.addWidget(splitter, 1)

    def _text_panel(self, placeholder: str) -> QPlainTextEdit:
        w = QPlainTextEdit()
        w.setReadOnly(True)
        w.setFont(theme.mono_font(self.font_size))
        w.setPlaceholderText(placeholder)
        return w

    # --------------------------------------------------------------- API
    def clear(self) -> None:
        self.result = None
        self.table.setRowCount(0)
        self._row_field, self._row_bit_parent = [], []
        self.layout_widget.set_boxes([])
        self.hex_view.clear_data()
        for w in (self.checks_view, self.messages_view, self.analysis_view):
            w.clear()

    def show_raw(self, data: bytes) -> None:
        """Show bytes without a decode (e.g. when the protocol definition is invalid)."""
        self.hex_view.set_data(data)

    def set_result(self, result: DecodeResult) -> None:
        self.result = result
        self.analysis_view.clear()
        c = theme.colors()
        self.table.setRowCount(0)
        self._row_field, self._row_bit_parent = [], []
        boxes: list[LayoutBox] = []
        regions = []

        for fi, f in enumerate(result.fields):
            color = theme.field_color(f.index) if f.defined else QColor(c.undefined)
            row = self.table.rowCount()
            self.table.insertRow(row)
            self._row_field.append(fi)
            self._row_bit_parent.append(-1)
            tooltip = "\n".join([f.description] + f.messages if f.description else f.messages)
            cells = [f.offset_text, format_hex(f.raw), f.name, f.type_text, f.display, f.interpretation,
                     STATUS_TEXT.get(f.status, "")]
            for col, text in enumerate(cells):
                item = make_item(text, tooltip or text, align_center=col in (0, 6))
                if col == 2:
                    item.setBackground(QBrush(color))
                if col == 6 or (col == 5 and f.status == "error"):
                    item.setForeground(QBrush(QColor({"ok": c.ok, "warning": c.warn, "error": c.error}
                                                     .get(f.status, c.muted))))
                if not f.defined and col in (4, 5):
                    item.setForeground(QBrush(QColor(c.muted)))
                self.table.setItem(row, col, item)

            for b in f.bits:
                brow = self.table.rowCount()
                self.table.insertRow(brow)
                self._row_field.append(fi)
                self._row_bit_parent.append(fi)
                bit_cells = [f"{f.offset}.{b.start}" if b.length == 1 else f"{f.offset}.{b.start + b.length - 1}-{b.start}",
                             "", f"  └ {b.name}", b.bit_label, str(b.value), b.interpretation, ""]
                for col, text in enumerate(bit_cells):
                    item = make_item(text, b.description or text, align_center=col == 0)
                    item.setForeground(QBrush(QColor(c.muted if col in (0, 3) else c.text)))
                    self.table.setItem(brow, col, item)

            if f.length > 0:
                regions.append((f.offset, f.length, color))
            boxes.append(LayoutBox(f.name if f.defined else "?", f.raw, color, f.status, row,
                                   f"{f.name}  [{f.offset_text}]\n{f.display}\n{f.interpretation}"))

        self.layout_widget.set_boxes(boxes)
        self.hex_view.set_data(result.data, regions)
        self.checks_view.setPlainText("\n\n".join("\n".join(ch.summary_lines()) for ch in result.checks)
                                      or "No CRC / checksum fields in this protocol.")
        msgs = [f"✗ {e}" for e in result.errors] + [f"⚠ {w}" for w in result.warnings]
        self.messages_view.setPlainText("\n".join(msgs) or "✓ No problems found.")
        if result.errors:
            self.side_tabs.setCurrentWidget(self.messages_view)
        elif result.checks:
            self.side_tabs.setCurrentWidget(self.checks_view)
        else:
            self.side_tabs.setCurrentWidget(self.hex_view)

    def set_analysis(self, text: str) -> None:
        self.analysis_view.setPlainText(text)
        self.side_tabs.setCurrentWidget(self.analysis_view)

    def copy_report(self) -> None:
        if self.result:
            QGuiApplication.clipboard().setText(self.result.to_text())

    def refresh_theme(self) -> None:
        if self.result:
            analysis = self.analysis_view.toPlainText()
            self.set_result(self.result)
            if analysis:
                self.analysis_view.setPlainText(analysis)
        else:
            self.hex_view.refresh_theme()

    # ---------------------------------------------------------- selection
    def _select_row(self, row: int) -> None:
        if 0 <= row < self.table.rowCount():
            self.table.selectRow(row)
            self.table.scrollToItem(self.table.item(row, 0))

    def _on_table_selection(self) -> None:
        if not self.result:
            return
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if not rows:
            return
        fi = self._row_field[rows[0]]
        f = self.result.fields[fi]
        table_row = rows[0] if self._row_bit_parent[rows[0]] < 0 else self._row_field.index(fi)
        self.layout_widget.select_row(table_row)
        self.hex_view.select_range(f.offset, f.length)
