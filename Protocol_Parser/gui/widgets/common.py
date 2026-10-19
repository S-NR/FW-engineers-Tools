"""Small reusable widgets: section headers, status indicator, copyable table."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QKeySequence
from PySide6.QtWidgets import QAbstractItemView, QLabel, QTableWidget, QTableWidgetItem

from gui import theme


class SectionHeader(QLabel):
    def __init__(self, text: str, parent=None) -> None:
        super().__init__(text.upper(), parent)
        self.setObjectName("SectionHeader")


class StatusIndicator(QLabel):
    """Coloured status pill: level is ok / warn / error / idle."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("StatusIndicator")
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._level, self._text = "idle", ""
        self.set_status("idle", "Ready")

    def set_status(self, level: str, text: str, tooltip: str = "") -> None:
        self._level, self._text = level, text
        c = theme.colors()
        color = {"ok": c.ok, "warn": c.warn, "error": c.error}.get(level, c.muted)
        self.setText(text)
        self.setToolTip(tooltip or text)
        self.setStyleSheet(f"color: {color}; border: 1px solid {color};")

    def refresh_theme(self) -> None:
        self.set_status(self._level, self._text, self.toolTip())


class CopyableTable(QTableWidget):
    """QTableWidget with Ctrl+C copying the selection as tab-separated text."""

    def __init__(self, rows: int = 0, columns: int = 0, parent=None) -> None:
        super().__init__(rows, columns, parent)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(24)
        self.setWordWrap(False)

    def keyPressEvent(self, event) -> None:
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copy_selection()
            return
        super().keyPressEvent(event)

    def _rows_text(self, rows: list[int], with_header: bool) -> str:
        lines = []
        if with_header:
            lines.append("\t".join(self.horizontalHeaderItem(c).text() if self.horizontalHeaderItem(c) else ""
                                   for c in range(self.columnCount())))
        for r in rows:
            cells = []
            for c in range(self.columnCount()):
                item = self.item(r, c)
                cells.append(item.text() if item else "")
            lines.append("\t".join(cells))
        return "\n".join(lines)

    def copy_selection(self) -> None:
        rows = sorted({i.row() for i in self.selectedIndexes()})
        if rows:
            QGuiApplication.clipboard().setText(self._rows_text(rows, with_header=False))

    def copy_all(self) -> None:
        QGuiApplication.clipboard().setText(self._rows_text(list(range(self.rowCount())), with_header=True))


def make_item(text: str, tooltip: str = "", align_center: bool = False) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setToolTip(tooltip or text)
    if align_center:
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    return item
