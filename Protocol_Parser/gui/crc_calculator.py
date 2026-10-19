"""CRC / checksum calculator and CRC-placement search tool."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QCheckBox, QDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout)

from core import checksum as checksum_mod
from core import crc as crc_mod
from gui import theme
from gui.widgets.common import CopyableTable, make_item
from gui.widgets.hex_input import HexInputEdit
from utils.hex_utils import format_hex, try_parse_hex


class CRCCalculatorDialog(QDialog):
    def __init__(self, initial_hex: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("CRC / Checksum Calculator")
        self.resize(820, 640)
        root = QVBoxLayout(self)
        root.addWidget(QLabel("Data to checksum (HEX). Results update as you type."))
        self.input = HexInputEdit(font_size=11, placeholder="e.g. 01 08 00 67 65 74 20 64 69 61 67")
        self.input.setMaximumHeight(70)
        self.input.setPlainText(initial_hex)
        self.input.textChanged.connect(self.recalculate)
        root.addWidget(self.input)

        custom = QGroupBox("Custom CRC")
        cf = QFormLayout(custom)
        row = QHBoxLayout()
        self.width = QSpinBox()
        self.width.setRange(8, 64)
        self.width.setValue(16)
        self.poly = QLineEdit("0x1021")
        self.init = QLineEdit("0xFFFF")
        self.xorout = QLineEdit("0x0000")
        self.refin = QCheckBox("Reflect in")
        self.refout = QCheckBox("Reflect out")
        for label, w in (("Width", self.width), ("Poly", self.poly), ("Init", self.init), ("XorOut", self.xorout)):
            row.addWidget(QLabel(label))
            row.addWidget(w)
        row.addWidget(self.refin)
        row.addWidget(self.refout)
        cf.addRow(row)
        self.custom_result = QLabel()
        self.custom_result.setFont(theme.mono_font(11))
        self.custom_result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        cf.addRow("Result:", self.custom_result)
        for w in (self.poly, self.init, self.xorout):
            w.textChanged.connect(self.recalculate)
        self.width.valueChanged.connect(self.recalculate)
        self.refin.toggled.connect(self.recalculate)
        self.refout.toggled.connect(self.recalculate)
        root.addWidget(custom)

        self.table = CopyableTable(0, 5)
        self.table.setHorizontalHeaderLabels(["Algorithm", "Value", "Big-endian bytes", "Little-endian bytes", "Notes"])
        self.table.setFont(theme.mono_font(10))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        root.addWidget(self.table, 1)

        search_row = QHBoxLayout()
        search = QPushButton("Search CRC placement in packet")
        search.setToolTip("Treat the input as a complete packet and try every preset CRC at the end "
                          "(optionally followed by 1-2 trailer bytes such as ETX)")
        search.clicked.connect(self.search)
        search_row.addWidget(search)
        search_row.addStretch()
        root.addLayout(search_row)
        self.search_result = QPlainTextEdit()
        self.search_result.setReadOnly(True)
        self.search_result.setMaximumHeight(100)
        self.search_result.setFont(theme.mono_font(10))
        root.addWidget(self.search_result)
        self.recalculate()

    def recalculate(self) -> None:
        data, err = try_parse_hex(self.input.toPlainText())
        self.table.setRowCount(0)
        if data is None:
            self.custom_result.setText(err or "")
            return
        for p in crc_mod.PRESETS.values():
            v = crc_mod.compute_crc(data, p)
            self._row(p.name, v, p.width, p.note)
        for a in checksum_mod.CHECKSUMS.values():
            self._row(a.name, a.func(data), a.width, a.description)
        try:
            params = crc_mod.resolve_crc({"algorithm": "custom", "width": self.width.value(),
                                          "poly": self.poly.text(), "init": self.init.text() or "0",
                                          "xorout": self.xorout.text() or "0", "refin": self.refin.isChecked(),
                                          "refout": self.refout.isChecked()})
            v = crc_mod.compute_crc(data, params)
            digits = (params.width + 3) // 4
            self.custom_result.setText(f"0x{v:0{digits}X}   BE: {format_hex(crc_mod.crc_to_bytes(v, params.width))}"
                                       f"   LE: {format_hex(crc_mod.crc_to_bytes(v, params.width, 'little'))}")
        except (crc_mod.CRCError, ValueError) as exc:
            self.custom_result.setText(f"Invalid parameters: {exc}")

    def _row(self, name: str, value: int, width: int, note: str) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        nbytes = (width + 7) // 8
        cells = (name, f"0x{value:0{nbytes * 2}X}", format_hex(value.to_bytes(nbytes, "big")),
                 format_hex(value.to_bytes(nbytes, "little")), note)
        for col, text in enumerate(cells):
            self.table.setItem(row, col, make_item(text))

    def search(self) -> None:
        data, err = try_parse_hex(self.input.toPlainText())
        if data is None:
            self.search_result.setPlainText(err or "Invalid HEX data")
            return
        matches = crc_mod.find_matching_crc([data])
        if not matches:
            self.search_result.setPlainText("No preset CRC matches the end of this packet.")
            return
        lines = [f"Match: {m.describe()}" for m in matches]
        lines.append("Note: one packet can match by coincidence (likely for 8-bit CRCs). "
                     "Confirm with more packets in the Diff Analyzer.")
        self.search_result.setPlainText("\n".join(lines))
        c = theme.colors()
        names = {m.algorithm for m in matches}
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).text() in names:
                self.table.item(row, 0).setForeground(QBrush(QColor(c.ok)))
