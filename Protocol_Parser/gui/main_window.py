"""Main application window."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QHBoxLayout, QLabel, QMainWindow,
                               QMessageBox, QPushButton, QSplitter, QStatusBar, QTabWidget, QVBoxLayout, QWidget)

import ai
from core.decoder import DecodeResult, decode_packet
from core.history import PacketHistory
from core.protocol import FieldDefinition, ProtocolDefinition, ProtocolError
from gui import theme
from gui.comparison_view import TxRxAnalyzerView
from gui.crc_calculator import CRCCalculatorDialog
from gui.decoded_view import DecodedView
from gui.diff_view import DiffAnalyzerView
from gui.history_view import HistoryView
from gui.packet_builder import PacketBuilderDialog
from gui.packet_input import PacketInputWidget
from gui.protocol_editor import ProtocolEditorDialog
from gui.settings import Settings
from gui.widgets.common import StatusIndicator
from protocols.protocol_manager import ProtocolManager
from utils.hex_utils import format_hex, try_parse_hex

APP_TITLE = "Firmware Protocol Decoder"


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, manager: ProtocolManager) -> None:
        super().__init__()
        self.settings = settings
        self.manager = manager
        self.history = PacketHistory()
        self.analyzer = ai.get_analyzer(settings.get("ai_backend"))
        font_size = int(settings.get("hex_font_size", 11))
        bytes_per_row = int(settings.get("bytes_per_row", 16))

        self.setWindowTitle(APP_TITLE)
        self.resize(1280, 860)

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 0)

        # ---- protocol bar -------------------------------------------------
        bar = QHBoxLayout()
        title = QLabel(APP_TITLE)
        title.setStyleSheet("font-size: 15px; font-weight: 700;")
        bar.addWidget(title)
        bar.addSpacing(24)
        bar.addWidget(QLabel("Protocol:"))
        self.protocol_combo = QComboBox()
        self.protocol_combo.setMinimumWidth(260)
        self.protocol_combo.setToolTip("Protocol definition used for decoding")
        self.protocol_combo.currentTextChanged.connect(self._on_protocol_changed)
        bar.addWidget(self.protocol_combo)
        for label, slot, tip in (("Edit", self.edit_protocol, "Edit the selected protocol"),
                                 ("New", self.new_protocol, "Create a new protocol definition"),
                                 ("Save", self.save_protocol, "Save the selected protocol as JSON (Ctrl+S)"),
                                 ("Import…", self.import_protocol, "Load a protocol JSON file")):
            b = QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            bar.addWidget(b)
        bar.addStretch()
        self.protocol_desc = QLabel()
        self.protocol_desc.setStyleSheet(f"color: {theme.colors().muted};")
        self.protocol_desc.setMaximumWidth(520)
        bar.addWidget(self.protocol_desc)
        root.addLayout(bar)

        # ---- tabs ---------------------------------------------------------
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        decoder_page = QWidget()
        dl = QVBoxLayout(decoder_page)
        dl.setContentsMargins(0, 0, 0, 0)
        self.decoder_splitter = QSplitter(Qt.Orientation.Vertical)
        self.input = PacketInputWidget(font_size=font_size)
        self.input.decodeRequested.connect(self.decode_current)
        self.input.buildRequested.connect(self.build_packet)
        self.input.fileLoaded.connect(lambda p: self.settings.set("last_dir", str(Path(p).parent)))
        self.input.last_dir = settings.get("last_dir", "")
        self.decoded = DecodedView(font_size=font_size, bytes_per_row=bytes_per_row)
        self.decoded.analyzeRequested.connect(self.analyze_current)
        self.decoder_splitter.addWidget(self.input)
        self.decoder_splitter.addWidget(self.decoded)
        self.decoder_splitter.setStretchFactor(0, 1)
        self.decoder_splitter.setStretchFactor(1, 4)
        self.decoder_splitter.setSizes([140, 700])
        dl.addWidget(self.decoder_splitter)
        self.tabs.addTab(decoder_page, "Decoder")

        self.txrx = TxRxAnalyzerView(self.current_protocol, font_size=font_size)
        self.txrx.packetDecoded.connect(lambda d, r: self._record(d, r))
        self.tabs.addTab(self.txrx, "TX/RX Analyzer")

        self.diff = DiffAnalyzerView(self.current_protocol, font_size=font_size)
        self.tabs.addTab(self.diff, "Packet Diff Analyzer")

        self.history_view = HistoryView(self.history, font_size=font_size)
        self.history_view.entryActivated.connect(self._load_history_entry)
        self.tabs.addTab(self.history_view, "History")

        self.setCentralWidget(central)

        # ---- status bar ---------------------------------------------------
        status = QStatusBar()
        self.status_indicator = StatusIndicator()
        status.addWidget(QLabel("Status:"))
        status.addWidget(self.status_indicator, 1)
        self.setStatusBar(status)

        self._build_menu()
        self._build_shortcuts()
        self._populate_protocols(settings.get("last_protocol"))

        # First launch: preloaded example packet.
        initial = settings.get("last_input") or settings.get("default_packet", "")
        self.input.set_text(initial)
        self.txrx.set_packets(*self._txrx_defaults())
        self.diff.set_text(self._diff_defaults())
        self.status_indicator.set_status("idle", "Ready - press Decode (Ctrl+Enter)")

        if settings.get("window_geometry"):
            self.restoreGeometry(QByteArray.fromBase64(settings.get("window_geometry").encode()))
        problems = list(manager.load_errors)
        if settings.load_error:
            problems.append(f"settings.json: {settings.load_error} (defaults used)")
        if problems:
            self.statusBar().showMessage("Some files could not be loaded: " + "; ".join(problems), 15000)

    # ------------------------------------------------------------- setup
    def _build_menu(self) -> None:
        mb = self.menuBar()
        file_menu = mb.addMenu("&File")
        self._action(file_menu, "Open Packet File…", self.input.open_file_dialog, QKeySequence("Ctrl+O"))
        self._action(file_menu, "Import Protocol…", self.import_protocol)
        self._action(file_menu, "Save Protocol", self.save_protocol, QKeySequence("Ctrl+S"))
        file_menu.addSeparator()
        self._action(file_menu, "Export History…", self.history_view.export)
        file_menu.addSeparator()
        self._action(file_menu, "Exit", self.close, QKeySequence("Ctrl+Q"))

        edit_menu = mb.addMenu("&Edit")
        self._action(edit_menu, "Decode", self._decode_active_tab, QKeySequence("Ctrl+Return"))
        self._action(edit_menu, "Clear Input", self.input.clear, QKeySequence("Ctrl+L"))
        self._action(edit_menu, "Copy Decoded Table", lambda: self.decoded.table.copy_all(), QKeySequence("Ctrl+Shift+C"))
        self._action(edit_menu, "Copy Report", self.decoded.copy_report)

        proto_menu = mb.addMenu("&Protocol")
        self._action(proto_menu, "Edit Protocol…", self.edit_protocol, QKeySequence("Ctrl+E"))
        self._action(proto_menu, "New Protocol…", self.new_protocol, QKeySequence("Ctrl+N"))
        self._action(proto_menu, "Reload Protocol Files", self.reload_protocols, QKeySequence("F5"))

        tools = mb.addMenu("&Tools")
        self._action(tools, "Build Packet…", self.build_packet, QKeySequence("Ctrl+B"))
        self._action(tools, "CRC / Checksum Calculator…", self.open_crc_calculator, QKeySequence("Ctrl+K"))
        self._action(tools, "Analyze Packet with AI", self.analyze_current)

        view = mb.addMenu("&View")
        group = QActionGroup(self)
        for name in ("dark", "light"):
            act = QAction(f"{name.title()} Theme", self, checkable=True)
            act.setChecked(self.settings.get("theme") == name)
            act.triggered.connect(lambda _=False, n=name: self.set_theme(n))
            group.addAction(act)
            view.addAction(act)
        view.addSeparator()
        for i, label in enumerate(("Decoder", "TX/RX Analyzer", "Packet Diff Analyzer", "History")):
            self._action(view, label, lambda _=False, i=i: self.tabs.setCurrentIndex(i), QKeySequence(f"Alt+{i + 1}"))

        help_menu = mb.addMenu("&Help")
        self._action(help_menu, "Keyboard Shortcuts", self.show_shortcuts, QKeySequence("F1"))
        self._action(help_menu, "About", self.show_about)

    def _action(self, menu, text, slot, shortcut: QKeySequence | None = None) -> QAction:
        act = QAction(text, self)
        if shortcut is not None:
            act.setShortcut(shortcut)
            act.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        act.triggered.connect(slot)
        menu.addAction(act)
        return act

    def _build_shortcuts(self) -> None:
        # Keypad Enter in addition to Ctrl+Return from the menu.
        QShortcut(QKeySequence("Ctrl+Enter"), self, activated=self._decode_active_tab)

    def _decode_active_tab(self) -> None:
        page = self.tabs.currentWidget()
        if page is self.txrx:
            self.txrx.analyze()
        elif page is self.diff:
            self.diff.analyze()
        else:
            self.decode_current()

    def _txrx_defaults(self) -> tuple[str, str]:
        p = self.current_protocol()
        if p:
            tx = next((e.hex for e in p.examples if e.direction == "TX"), "")
            rx = next((e.hex for e in p.examples if e.direction == "RX"), "")
            return tx, rx
        return "", ""

    def _diff_defaults(self) -> str:
        sensor = self.manager.get("Simple Sensor Packet")
        return "\n".join(e.hex for e in sensor.examples) if sensor else ""

    # ---------------------------------------------------------- protocols
    def current_protocol(self) -> ProtocolDefinition | None:
        return self.manager.get(self.protocol_combo.currentText())

    def _populate_protocols(self, select: str | None = None) -> None:
        self.protocol_combo.blockSignals(True)
        self.protocol_combo.clear()
        names = self.manager.names()
        self.protocol_combo.addItems(names)
        self.protocol_combo.blockSignals(False)
        if select and select in names:
            self.protocol_combo.setCurrentText(select)
        elif names:
            self.protocol_combo.setCurrentIndex(0)
        self._on_protocol_changed(self.protocol_combo.currentText())

    def _on_protocol_changed(self, name: str) -> None:
        p = self.manager.get(name)
        self.input.set_examples(p)
        if p:
            self.settings.set("last_protocol", p.name)
            self.protocol_desc.setText(p.description)
            self.protocol_desc.setToolTip(f"{p.description}\n\nFile: {p.source_path or '(unsaved)'}")
            errors = p.errors()
            if errors:
                self.status_indicator.set_status("error", f"Invalid protocol definition: {errors[0]}",
                                                 "\n".join(errors))
        else:
            self.protocol_desc.setText("")

    def reload_protocols(self) -> None:
        current = self.protocol_combo.currentText()
        self.manager.reload()
        self._populate_protocols(current)
        msg = f"Reloaded {len(self.manager.names())} protocol(s)"
        if self.manager.load_errors:
            msg += " - errors: " + "; ".join(self.manager.load_errors)
        self.statusBar().showMessage(msg, 8000)

    def edit_protocol(self) -> None:
        p = self.current_protocol()
        if p is None:
            self.new_protocol()
            return
        self._run_editor(p, is_new=False)

    def new_protocol(self) -> None:
        template = ProtocolDefinition(
            name="New Protocol", description="", default_endian="big",
            fields=[FieldDefinition("STX", "hex", offset=0, length=1, description="Start of frame"),
                    FieldDefinition("Command", "uint8", description="Command code"),
                    FieldDefinition("Length", "uint8", description="Payload length"),
                    FieldDefinition("Payload", "hex", length_from="Length", description="Payload")])
        self._run_editor(template, is_new=True)

    def _run_editor(self, protocol: ProtocolDefinition, is_new: bool) -> None:
        dlg = ProtocolEditorDialog(protocol, self.manager, is_new=is_new, parent=self)
        if dlg.exec() and dlg.saved_protocol is not None:
            self._populate_protocols(dlg.saved_protocol.name)
            self.statusBar().showMessage(f"Saved protocol '{dlg.saved_protocol.name}' to "
                                         f"{dlg.saved_protocol.source_path}", 8000)
            if self.decoded.result is not None:
                self.decode_current()

    def save_protocol(self) -> None:
        p = self.current_protocol()
        if p is None:
            return
        try:
            path = self.manager.save(p)
        except OSError as exc:
            QMessageBox.warning(self, "Save protocol", f"Cannot save: {exc}")
            return
        self.statusBar().showMessage(f"Saved '{p.name}' to {path}", 6000)

    def import_protocol(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import protocol", self.settings.get("last_dir", ""),
                                              "Protocol JSON (*.json);;All files (*)")
        if not path:
            return
        try:
            p = self.manager.import_file(path)
        except (ProtocolError, ValueError, OSError) as exc:
            QMessageBox.warning(self, "Invalid protocol definition", str(exc))
            return
        self._populate_protocols(p.name)
        self.statusBar().showMessage(f"Imported '{p.name}' (use Save to keep a copy)", 6000)

    # ------------------------------------------------------------ decoding
    def decode_current(self) -> None:
        protocol = self.current_protocol()
        text = self.input.text()
        data, err = try_parse_hex(text)
        if data is None:
            self.decoded.clear()
            self.status_indicator.set_status("error", f"✗ Invalid HEX data: {err}", err or "")
            return
        if protocol is None:
            self.decoded.clear()
            self.decoded.show_raw(data)
            self.status_indicator.set_status("error", "✗ No protocol selected")
            return
        result = decode_packet(protocol, data)
        self.decoded.set_result(result)
        self._show_status(result)
        self.settings.set("last_input", text)
        self._record(self.input.current_direction(), result)

    def ingest_packet(self, data: bytes, direction: str = "RX") -> DecodeResult | None:
        """Hook for live transports: decode a received packet and record it in the history."""
        protocol = self.current_protocol()
        if protocol is None:
            return None
        result = decode_packet(protocol, data)
        self._record(direction, result)
        return result

    def _show_status(self, result: DecodeResult) -> None:
        level = "ok" if result.ok and not result.warnings else ("warn" if result.ok else "error")
        tip = "\n".join([f"✗ {e}" for e in result.errors] + [f"⚠ {w}" for w in result.warnings])
        self.status_indicator.set_status(level, result.status_message(), tip)

    def _record(self, direction: str, result: DecodeResult) -> None:
        if not self.settings.get("auto_add_history", True):
            return
        summary = "; ".join(f'{f.name}="{f.value}"' for f in result.fields
                            if f.defined and f.field_type in ("ascii", "utf8"))
        entry = self.history.add(direction or "-", result.data, result.status, result.protocol_name, summary)
        self.history_view.add_entry(entry)

    def _load_history_entry(self, entry) -> None:
        if entry.protocol in self.manager.names():
            self.protocol_combo.setCurrentText(entry.protocol)
        self.input.set_text(format_hex(entry.data))
        self.input.set_direction(entry.direction)
        self.tabs.setCurrentIndex(0)
        protocol = self.current_protocol()
        if protocol:
            result = decode_packet(protocol, entry.data)
            self.decoded.set_result(result)
            self._show_status(result)

    def analyze_current(self) -> None:
        if self.decoded.result is None:
            self.decode_current()
        result = self.decoded.result
        if result is None:
            return
        try:
            report = self.analyzer.analyze(result, self.input.current_direction())
        except ai.AnalyzerUnavailable as exc:
            self.decoded.set_analysis(f"Analyzer unavailable: {exc}")
            return
        self.decoded.set_analysis(report.to_text())
        self.tabs.setCurrentIndex(0)

    # --------------------------------------------------------------- tools
    def build_packet(self) -> None:
        protocol = self.current_protocol()
        if protocol is None:
            return
        errors = protocol.errors()
        if errors:
            QMessageBox.warning(self, "Build packet", "Invalid protocol definition:\n" + "\n".join(errors))
            return
        initial: dict[str, str] = {}
        if self.decoded.result is not None and self.decoded.result.protocol_name == protocol.name:
            initial = _initial_values(protocol, self.decoded.result)
        dlg = PacketBuilderDialog(protocol, initial, self)
        if dlg.exec() and dlg.packet is not None:
            self.input.set_text(format_hex(dlg.packet))
            self.decode_current()

    def open_crc_calculator(self) -> None:
        data, _ = try_parse_hex(self.input.text())
        CRCCalculatorDialog(format_hex(data) if data else "", self).exec()

    def set_theme(self, name: str) -> None:
        theme.apply_theme(QApplication.instance(), name)
        self.settings.set("theme", name)
        self.protocol_desc.setStyleSheet(f"color: {theme.colors().muted};")
        self.status_indicator.refresh_theme()
        self.decoded.refresh_theme()
        self.txrx.refresh_theme()
        self.history_view.rebuild()
        if self.diff.report is not None:
            self.diff.analyze()

    def show_shortcuts(self) -> None:
        QMessageBox.information(self, "Keyboard Shortcuts", "\n".join([
            "Ctrl+Enter      Decode (or analyze, in TX/RX and Diff tabs)",
            "Ctrl+O          Open packet file",
            "Ctrl+S          Save protocol",
            "Ctrl+C          Copy selected data (tables / text)",
            "Ctrl+Shift+C    Copy whole decoded table",
            "Ctrl+L          Clear input",
            "Ctrl+E / Ctrl+N Edit / new protocol",
            "Ctrl+B          Build packet",
            "Ctrl+K          CRC / checksum calculator",
            "Alt+1..4        Switch tabs",
            "F5              Reload protocol files",
        ]))

    def show_about(self) -> None:
        QMessageBox.about(self, "About", f"<b>{APP_TITLE}</b><br>Deterministic, definition-driven packet decoder "
                                         "for UART, USB-CDC, RS485, CAN, Modbus and custom binary protocols.<br><br>"
                                         f"Analysis backend: {self.analyzer.name}")

    def closeEvent(self, event) -> None:
        self.settings.set("window_geometry", bytes(self.saveGeometry().toBase64()).decode())
        self.settings.save()
        super().closeEvent(event)


def _initial_values(protocol: ProtocolDefinition, result: DecodeResult) -> dict[str, str]:
    """Pre-fill the packet builder with values from the current decode."""
    values = {}
    for f in protocol.fields:
        df = result.field(f.name)
        if df is None or df.value is None or df.status == "error" and not df.raw:
            continue
        if f.type in ("ascii", "utf8"):
            values[f.name] = str(df.value)
        elif f.type in ("hex", "binary"):
            values[f.name] = format_hex(df.raw)
            if f.type == "binary":
                values[f.name] = " ".join(f"{b:08b}" for b in df.raw)
        elif f.type == "bool":
            values[f.name] = "1" if df.value else "0"
        elif isinstance(df.value, (int, float)):
            v = df.value
            if f.scale is not None or f.bias is not None:
                v = v * (f.scale if f.scale is not None else 1) + (f.bias or 0)
                values[f.name] = f"{v:.6g}"
            else:
                values[f.name] = str(v)
    return values
