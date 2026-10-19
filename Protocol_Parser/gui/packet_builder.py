"""Packet Builder dialog: enter field values, get a packet with length/CRC filled in."""
from __future__ import annotations

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QPushButton,
                               QVBoxLayout)

from core.encoder import EncodeError, encode_packet, user_fields
from core.protocol import ProtocolDefinition, type_label
from gui import theme
from utils.hex_utils import format_hex


class PacketBuilderDialog(QDialog):
    def __init__(self, protocol: ProtocolDefinition, initial: dict[str, str] | None = None, parent=None) -> None:
        super().__init__(parent)
        self.protocol = protocol
        self.packet: bytes | None = None
        self.setWindowTitle(f"Build Packet - {protocol.name}")
        self.resize(560, 360)

        root = QVBoxLayout(self)
        root.addWidget(QLabel("Frame bytes, length fields and CRC/checksum fields are filled in automatically."))
        form = QFormLayout()
        self.edits: dict[str, QLineEdit] = {}
        for f in user_fields(protocol):
            edit = QLineEdit((initial or {}).get(f.name, ""))
            hint = {"ascii": "text", "utf8": "text", "hex": "HEX bytes, e.g. 00 0A",
                    "binary": "bits, e.g. 10100101"}.get(f.type, "number (decimal or 0x..)")
            if f.values:
                hint += " or name: " + ", ".join(list(f.values.values())[:3])
            if f.scale or f.bias:
                hint += f" (engineering value{', ' + f.unit if f.unit else ''})"
            edit.setPlaceholderText(hint)
            edit.setToolTip(f.description or f.name)
            edit.textChanged.connect(self._rebuild)
            form.addRow(f"{f.name} [{type_label(f.type)}]:", edit)
            self.edits[f.name] = edit
        root.addLayout(form)

        self.output = QLineEdit()
        self.output.setReadOnly(True)
        self.output.setFont(theme.mono_font(11))
        root.addWidget(QLabel("Packet:"))
        root.addWidget(self.output)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet(f"color: {theme.colors().error}")
        root.addWidget(self.error)

        copy_btn = QPushButton("Copy")
        copy_btn.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.output.text()))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Use Packet")
        buttons.addButton(copy_btn, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        root.addWidget(buttons)
        self._rebuild()

    def _rebuild(self) -> None:
        values = {name: e.text() for name, e in self.edits.items()}
        try:
            self.packet = encode_packet(self.protocol, values)
            self.output.setText(format_hex(self.packet))
            self.error.setText("")
        except EncodeError as exc:
            self.packet = None
            self.output.setText("")
            self.error.setText(str(exc))
        self.ok_button.setEnabled(self.packet is not None)

    def _accept(self) -> None:
        if self.packet is not None:
            self.accept()
