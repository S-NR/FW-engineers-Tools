"""Visual packet layout: one box per field, colours shared with the decode table."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from gui import theme
from utils.hex_utils import format_hex


@dataclass
class LayoutBox:
    name: str
    raw: bytes
    color: QColor
    status: str  # ok | warning | error | undefined
    row: int  # decode-table row this box represents
    tooltip: str = ""


class PacketLayoutWidget(QWidget):
    fieldClicked = Signal(int)  # table row

    PAD = 10
    MAX_HEX_BYTES = 6

    def __init__(self, parent=None, font_size: int = 10) -> None:
        super().__init__(parent)
        self._boxes: list[LayoutBox] = []
        self._rects: list[QRectF] = []
        self._selected_row = -1
        self.setFont(theme.mono_font(font_size))
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setToolTip("Packet layout - click a field to select it in the table")
        self._layout()  # fixes the height before the first packet is shown

    def set_boxes(self, boxes: list[LayoutBox]) -> None:
        self._boxes = boxes
        self._selected_row = -1
        self._layout()
        self.update()

    def select_row(self, row: int) -> None:
        self._selected_row = row
        self.update()

    def _hex_text(self, raw: bytes) -> str:
        if not raw:
            return "--"
        if len(raw) <= self.MAX_HEX_BYTES:
            return format_hex(raw)
        return format_hex(raw[: self.MAX_HEX_BYTES - 2]) + " … " + format_hex(raw[-1:])

    def _layout(self) -> None:
        fm = QFontMetrics(self.font())
        x = 4.0
        h = fm.height() * 2 + 18
        self._rects = []
        for box in self._boxes:
            w = max(fm.horizontalAdvance(box.name), fm.horizontalAdvance(self._hex_text(box.raw))) + 2 * self.PAD
            self._rects.append(QRectF(x, 4, w, h))
            x += w
        self.setMinimumWidth(int(x + 8))
        self.setFixedHeight(int(h + 10))

    def sizeHint(self) -> QSize:
        return QSize(max(self.minimumWidth(), 200), self.height() or 60)

    def paintEvent(self, event) -> None:
        if not self._boxes:
            p = QPainter(self)
            p.setPen(QColor(theme.colors().muted))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Decode a packet to see its layout")
            return
        c = theme.colors()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        fm = QFontMetrics(self.font())
        border = QColor(c.border)
        for box, rect in zip(self._boxes, self._rects):
            p.fillRect(rect, box.color)
            mid = rect.top() + rect.height() / 2
            p.setPen(QPen(border, 1))
            p.drawRect(rect)
            p.drawLine(int(rect.left()), int(mid), int(rect.right()), int(mid))
            if box.status in ("error", "warning"):
                strip = QColor(c.error if box.status == "error" else c.warn)
                p.fillRect(QRectF(rect.left() + 1, rect.top() + 1, rect.width() - 1, 3), strip)
            p.setPen(QColor(c.text))
            name_rect = QRectF(rect.left(), rect.top(), rect.width(), rect.height() / 2)
            hex_rect = QRectF(rect.left(), mid, rect.width(), rect.height() / 2)
            f = p.font()
            f.setBold(True)
            p.setFont(f)
            p.drawText(name_rect, Qt.AlignmentFlag.AlignCenter, fm.elidedText(box.name, Qt.TextElideMode.ElideRight,
                                                                             int(rect.width() - 6)))
            f.setBold(False)
            p.setFont(f)
            p.drawText(hex_rect, Qt.AlignmentFlag.AlignCenter, self._hex_text(box.raw))
            if box.row == self._selected_row:
                p.setPen(QPen(QColor(c.accent), 2))
                p.drawRect(rect.adjusted(1, 1, -1, -1))

    def _box_at(self, pos) -> int:
        for i, rect in enumerate(self._rects):
            if rect.contains(pos):
                return i
        return -1

    def mousePressEvent(self, event) -> None:
        i = self._box_at(event.position())
        if i >= 0:
            self._selected_row = self._boxes[i].row
            self.update()
            self.fieldClicked.emit(self._boxes[i].row)

    def mouseMoveEvent(self, event) -> None:
        i = self._box_at(event.position())
        self.setToolTip(self._boxes[i].tooltip if i >= 0 else "Packet layout - click a field to select it")
