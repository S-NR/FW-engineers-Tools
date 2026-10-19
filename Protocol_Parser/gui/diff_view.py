"""Packet Difference Analyzer tab: find what changes across several packets."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPlainTextEdit,
                               QPushButton, QSplitter, QVBoxLayout, QWidget)

from core.decoder import decode_packet
from core.diff_analyzer import DiffReport, analyze_packets
from core.protocol import ProtocolDefinition
from gui import theme
from gui.packet_input import ask_packet_file, load_packets_or_warn
from gui.widgets.common import CopyableTable, SectionHeader, make_item
from gui.widgets.hex_input import HexInputEdit
from utils.hex_utils import format_hex, try_parse_hex


class DiffAnalyzerView(QWidget):
    def __init__(self, protocol_provider: Callable[[], ProtocolDefinition | None], font_size: int = 11,
                 parent=None) -> None:
        super().__init__(parent)
        self.protocol_provider = protocol_provider
        self.report: DiffReport | None = None
        self.field_names: dict[int, str] = {}
        self.last_dir = ""
        self.font_size = font_size

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.addWidget(SectionHeader("Packets (one per line)"))
        self.input = HexInputEdit(font_size=font_size, placeholder=(
            "Enter two or more packets, one per line, e.g.\n"
            "02 01 10 00 64 01 00 00 20 00\n02 01 10 00 64 01 00 00 21 00\n02 01 10 00 64 01 00 00 22 00"))
        self.input.decodeRequested.connect(self.analyze)
        self.input.fileDropped.connect(self.load_file)
        self.input.setMaximumHeight(140)
        root.addWidget(self.input)

        buttons = QHBoxLayout()
        analyze = QPushButton("Analyze")
        analyze.setObjectName("Primary")
        analyze.setToolTip("Compare all packets (Ctrl+Enter)")
        analyze.clicked.connect(self.analyze)
        load = QPushButton("Load File…")
        load.setToolTip("Load every packet found in a capture/log file")
        load.clicked.connect(self.open_file)
        clear = QPushButton("Clear")
        clear.clicked.connect(self.clear)
        self.only_varying = QCheckBox("Show only changing offsets")
        self.only_varying.setChecked(True)
        self.only_varying.toggled.connect(self._fill_table)
        for w in (analyze, load, clear):
            buttons.addWidget(w)
        buttons.addSpacing(16)
        buttons.addWidget(self.only_varying)
        buttons.addStretch()
        self.info = QLabel("")
        buttons.addWidget(self.info)
        root.addLayout(buttons)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.table = CopyableTable(0, 0)
        self.table.setFont(theme.mono_font(font_size))
        splitter.addWidget(self.table)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(SectionHeader("Findings"))
        self.findings = QPlainTextEdit()
        self.findings.setReadOnly(True)
        self.findings.setFont(theme.mono_font(font_size))
        rl.addWidget(self.findings)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter, 1)

    def set_text(self, text: str) -> None:
        self.input.setPlainText(text)

    def clear(self) -> None:
        self.input.clear()
        self.table.setRowCount(0)
        self.table.setColumnCount(0)
        self.findings.clear()
        self.info.setText("")
        self.report = None

    def open_file(self) -> None:
        path = ask_packet_file(self, self.last_dir)
        if path:
            self.load_file(path)

    def load_file(self, path: str) -> None:
        packets = load_packets_or_warn(self, path)
        if packets:
            self.last_dir = str(Path(path).parent)
            self.set_text("\n".join(format_hex(p.data) for p in packets))
            self.analyze()

    def _packets(self) -> list[bytes] | None:
        packets = []
        for n, line in enumerate(self.input.toPlainText().splitlines(), 1):
            if not line.strip() or line.strip().startswith(("#", "//")):
                continue
            data, err = try_parse_hex(line)
            if data is None:
                QMessageBox.warning(self, "Invalid HEX data", f"Packet on line {n}: {err}")
                return None
            packets.append(data)
        return packets

    def analyze(self) -> None:
        packets = self._packets()
        if packets is None:
            return
        if len(packets) < 2:
            QMessageBox.information(self, "Packet Difference Analyzer", "Enter at least two packets (one per line).")
            return
        self.report = analyze_packets(packets)
        self.field_names = {}
        protocol = self.protocol_provider()
        first = decode_packet(protocol, packets[0]) if protocol is not None else None
        # Only annotate with field names when packet 1 actually matches the protocol.
        if first is not None and first.ok:
            for f in first.fields:
                if f.defined:
                    for i in range(f.offset, f.end):
                        self.field_names[i] = f.name
        self.info.setText(f"{self.report.packet_count} packets, {len(self.report.varying_offsets)} changing offsets")
        self._fill_table()
        text = self.report.to_text(self.field_names)
        if self.field_names:
            text += f"\n\nField names from decoding packet 1 with '{protocol.name}'."
        elif first is not None:
            text += f"\n\nPacket 1 does not decode cleanly with '{protocol.name}', so no field names are shown."
        self.findings.setPlainText(text)

    def _fill_table(self) -> None:
        report = self.report
        if report is None:
            return
        c = theme.colors()
        n = report.packet_count
        headers = ["Offset", "Field"] + [f"P{i}" for i in range(1, n + 1)] + ["Pattern", "Possible interpretation"]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(0)
        for o in report.offsets:
            if self.only_varying.isChecked() and not o.varying:
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            cells = [str(o.offset), self.field_names.get(o.offset, "")]
            cells += ["--" if v is None else f"{v:02X}" for v in o.values]
            cells += [o.pattern, o.interpretation.replace("Possible interpretation: ", "")]
            for col, text in enumerate(cells):
                item = make_item(text, align_center=1 < col < 2 + n or col == 0)
                if o.varying and 2 <= col < 2 + n:
                    item.setBackground(QBrush(QColor(c.diff)))
                if col == len(cells) - 1 and text:
                    item.setToolTip("Possible interpretation (inferred, not verified): " + text)
                    item.setForeground(QBrush(QColor(c.muted)))
                self.table.setItem(row, col, item)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(len(headers) - 1, QHeaderView.ResizeMode.Stretch)
