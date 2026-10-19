"""Raw packet input panel (Decoder tab)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QMenu, QMessageBox,
                               QPushButton, QToolButton, QVBoxLayout, QWidget)

from core.protocol import ProtocolDefinition
from gui.widgets.common import SectionHeader
from gui.widgets.hex_input import HexInputEdit
from utils.file_utils import PACKET_FILE_FILTER, ExtractedPacket, load_packets_from_file
from utils.hex_utils import format_hex, try_parse_hex


def ask_packet_file(parent: QWidget, start_dir: str = "") -> str | None:
    path, _ = QFileDialog.getOpenFileName(parent, "Open packet file", start_dir, PACKET_FILE_FILTER)
    return path or None


def load_packets_or_warn(parent: QWidget, path: str) -> list[ExtractedPacket]:
    try:
        packets = load_packets_from_file(path)
    except OSError as exc:
        QMessageBox.warning(parent, "Load file", f"Cannot read {Path(path).name}:\n{exc}")
        return []
    if not packets:
        QMessageBox.warning(parent, "Load file", f"No HEX packet data found in {Path(path).name}.")
    return packets


def choose_packet(parent: QWidget, packets: list[ExtractedPacket], title: str) -> ExtractedPacket | None:
    if len(packets) == 1:
        return packets[0]
    items = []
    for i, p in enumerate(packets, 1):
        preview = format_hex(p.data[:12]) + (" …" if len(p.data) > 12 else "")
        direction = f"{p.direction} " if p.direction else ""
        items.append(f"#{i}  line {p.line_no}  {direction}({len(p.data)} B)  {preview}")
    item, ok = QInputDialog.getItem(parent, title, f"{len(packets)} packets found - select one:", items, 0, False)
    if not ok:
        return None
    return packets[items.index(item)]


class PacketInputWidget(QWidget):
    decodeRequested = Signal()
    buildRequested = Signal()
    fileLoaded = Signal(str)  # path, so the main window can remember the directory

    def __init__(self, font_size: int = 11, parent=None) -> None:
        super().__init__(parent)
        self.last_dir = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 2)

        header = QHBoxLayout()
        header.addWidget(SectionHeader("Raw packet"))
        header.addStretch()
        self.byte_count = QLabel("")
        self.byte_count.setToolTip("Number of bytes in the normalised input")
        header.addWidget(self.byte_count)
        layout.addLayout(header)

        self.editor = HexInputEdit(font_size=font_size)
        self.editor.setMinimumHeight(56)
        self.editor.decodeRequested.connect(self.decodeRequested)
        self.editor.fileDropped.connect(self.load_file)
        self.editor.textChanged.connect(self._update_count)
        layout.addWidget(self.editor)

        buttons = QHBoxLayout()
        self.decode_btn = QPushButton("Decode")
        self.decode_btn.setObjectName("Primary")
        self.decode_btn.setToolTip("Decode with the selected protocol (Ctrl+Enter)")
        self.decode_btn.clicked.connect(self.decodeRequested)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setToolTip("Clear the input (Ctrl+L)")
        self.clear_btn.clicked.connect(self.clear)
        self.load_btn = QPushButton("Load File…")
        self.load_btn.setToolTip("Load a packet from .txt/.hex/.log/.csv/.bin (Ctrl+O) - drag & drop also works")
        self.load_btn.clicked.connect(self.open_file_dialog)
        self.copy_btn = QPushButton("Copy")
        self.copy_btn.setToolTip("Copy the normalised HEX to the clipboard")
        self.copy_btn.clicked.connect(self.copy_hex)
        self.format_btn = QPushButton("Normalize")
        self.format_btn.setToolTip("Rewrite the input as space-separated HEX bytes")
        self.format_btn.clicked.connect(self.normalize)
        self.examples_btn = QToolButton()
        self.examples_btn.setText("Examples ▾")
        self.examples_btn.setToolTip("Load an example packet stored in the protocol definition")
        self.examples_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.examples_menu = QMenu(self)
        self.examples_btn.setMenu(self.examples_menu)
        self.build_btn = QPushButton("Build…")
        self.build_btn.setToolTip("Build a packet from field values (length and CRC filled in automatically)")
        self.build_btn.clicked.connect(self.buildRequested)

        self.direction = QComboBox()
        self.direction.addItems(["TX", "RX", "-"])
        self.direction.setToolTip("Direction recorded in the packet history")

        for w in (self.decode_btn, self.clear_btn, self.load_btn, self.copy_btn, self.format_btn,
                  self.examples_btn, self.build_btn):
            buttons.addWidget(w)
        buttons.addStretch()
        buttons.addWidget(QLabel("Direction:"))
        buttons.addWidget(self.direction)
        layout.addLayout(buttons)

    # --------------------------------------------------------------- API
    def text(self) -> str:
        return self.editor.toPlainText()

    def set_text(self, text: str) -> None:
        self.editor.setPlainText(text)

    def set_direction(self, direction: str) -> None:
        idx = self.direction.findText(direction or "-")
        if idx >= 0:
            self.direction.setCurrentIndex(idx)

    def current_direction(self) -> str:
        d = self.direction.currentText()
        return "" if d == "-" else d

    def clear(self) -> None:
        self.editor.clear()
        self.editor.setFocus()

    def set_examples(self, protocol: ProtocolDefinition | None) -> None:
        self.examples_menu.clear()
        examples = protocol.examples if protocol else []
        for ex in examples:
            action = QAction(f"{ex.name}   [{ex.direction or '-'}]", self.examples_menu)
            action.triggered.connect(lambda _=False, e=ex: self._load_example(e))
            self.examples_menu.addAction(action)
        self.examples_btn.setEnabled(bool(examples))

    def _load_example(self, ex) -> None:
        self.set_text(ex.hex)
        if ex.direction:
            self.set_direction(ex.direction)
        self.decodeRequested.emit()

    def _update_count(self) -> None:
        data, err = try_parse_hex(self.text())
        if data is not None:
            self.byte_count.setText(f"{len(data)} bytes")
            self.byte_count.setToolTip("Number of bytes in the normalised input")
        elif self.text().strip():
            self.byte_count.setText("invalid HEX")
            self.byte_count.setToolTip(err or "")
        else:
            self.byte_count.setText("")

    def copy_hex(self) -> None:
        data, _ = try_parse_hex(self.text())
        QGuiApplication.clipboard().setText(format_hex(data) if data else self.text())

    def normalize(self) -> None:
        data, err = try_parse_hex(self.text())
        if data is None:
            QMessageBox.warning(self, "Invalid HEX data", err or "Invalid HEX data")
            return
        self.set_text(format_hex(data))

    def open_file_dialog(self) -> None:
        path = ask_packet_file(self, self.last_dir)
        if path:
            self.load_file(path)

    def load_file(self, path: str) -> None:
        packets = load_packets_or_warn(self, path)
        if not packets:
            return
        self.last_dir = str(Path(path).parent)
        self.fileLoaded.emit(path)
        packet = choose_packet(self, packets, f"Load packet - {Path(path).name}")
        if packet is None:
            return
        self.set_text(format_hex(packet.data))
        if packet.direction:
            self.set_direction(packet.direction)
