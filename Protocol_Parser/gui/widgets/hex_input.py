"""Monospaced HEX entry box with file drag-and-drop and Ctrl+Enter."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QPlainTextEdit

from gui import theme


class HexInputEdit(QPlainTextEdit):
    decodeRequested = Signal()
    fileDropped = Signal(str)

    def __init__(self, parent=None, font_size: int = 11, placeholder: str = "") -> None:
        super().__init__(parent)
        self.setFont(theme.mono_font(font_size))
        self.setAcceptDrops(True)
        self.setPlaceholderText(placeholder or
                                "Paste HEX here, e.g.  02 01 08 00 ...   0x02 0x01 ...   02,01,08 ...\n"
                                "or drop a .txt / .hex / .log / .csv file")
        self.setTabChangesFocus(True)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and \
                event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.decodeRequested.emit()
            return
        super().keyPressEvent(event)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        urls = [u for u in event.mimeData().urls() if u.isLocalFile()]
        if urls:
            event.acceptProposedAction()
            self.fileDropped.emit(urls[0].toLocalFile())
        else:
            super().dropEvent(event)
