"""Packet History tab."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget

from core.history import HistoryEntry, PacketHistory
from gui import theme
from gui.widgets.common import CopyableTable, SectionHeader, make_item
from utils.hex_utils import format_hex


class HistoryView(QWidget):
    entryActivated = Signal(object)  # HistoryEntry

    COLUMNS = ["Time", "Direction", "Length", "Status", "Protocol", "Summary", "Data"]

    def __init__(self, history: PacketHistory, font_size: int = 11, parent=None) -> None:
        super().__init__(parent)
        self.history = history
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        top = QHBoxLayout()
        top.addWidget(SectionHeader("Session packet history"))
        top.addStretch()
        self.count = QLabel("0 packets")
        top.addWidget(self.count)
        clear = QPushButton("Clear History")
        clear.clicked.connect(self.clear)
        export = QPushButton("Export History…")
        export.setToolTip("Export the history to CSV")
        export.clicked.connect(self.export)
        top.addWidget(clear)
        top.addWidget(export)
        root.addLayout(top)

        self.table = CopyableTable(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setFont(theme.mono_font(font_size))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self.table.setToolTip("Double-click an entry to load it into the decoder")
        self.table.doubleClicked.connect(lambda idx: self.entryActivated.emit(self.history[idx.row()]))
        root.addWidget(self.table)

    def add_entry(self, entry: HistoryEntry) -> None:
        c = theme.colors()
        row = self.table.rowCount()
        self.table.insertRow(row)
        cells = [entry.time_text, entry.direction, str(entry.length), entry.status, entry.protocol,
                 entry.summary, format_hex(entry.data)]
        status_color = c.ok if entry.status.startswith("VALID") else c.error
        for col, text in enumerate(cells):
            item = make_item(text, align_center=col in (1, 2))
            if col == 3:
                item.setForeground(QBrush(QColor(status_color)))
            self.table.setItem(row, col, item)
        self.table.scrollToBottom()
        self.count.setText(f"{len(self.history)} packets")

    def rebuild(self) -> None:
        self.table.setRowCount(0)
        for e in self.history:
            self.add_entry(e)
        self.count.setText(f"{len(self.history)} packets")

    def clear(self) -> None:
        self.history.clear()
        self.table.setRowCount(0)
        self.count.setText("0 packets")

    def export(self) -> None:
        if not len(self.history):
            QMessageBox.information(self, "Export History", "The history is empty.")
            return
        default = f"packet_history_{datetime.now():%Y%m%d_%H%M%S}.csv"
        path, _ = QFileDialog.getSaveFileName(self, "Export History", default, "CSV (*.csv)")
        if not path:
            return
        try:
            n = self.history.export_csv(path)
        except OSError as exc:
            QMessageBox.warning(self, "Export History", f"Cannot write file: {exc}")
            return
        QMessageBox.information(self, "Export History", f"Exported {n} entries to\n{path}")
