"""Protocol editor dialog: field table, field editor, and raw JSON view."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
                               QPlainTextEdit, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget,
                               QVBoxLayout, QWidget)

from core import checksum as checksum_mod
from core import crc as crc_mod
from core.protocol import (FIELD_TYPES, TYPE_SIZES, BitDefinition, CheckConfig, FieldDefinition, FrameSpec,
                           ProtocolDefinition, ProtocolError, type_label)
from gui import theme
from gui.widgets.common import CopyableTable, make_item
from protocols.protocol_manager import ProtocolManager
from utils.hex_utils import HexParseError, format_hex, parse_hex_bytes, parse_int

OFFSET_MODES = [("Auto (after previous field)", "auto"), ("Absolute offset", "abs"),
                ("Last (ends at packet end)", "last"), ("From end (negative)", "end")]
LENGTH_MODES = [("Type size / fixed", "fixed"), ("From length field", "from"), ("Remaining bytes", "remaining")]


def _parse_values_text(text: str) -> dict[int, str]:
    """``1 = Read Coils`` per line -> {1: 'Read Coils'}."""
    values: dict[int, str] = {}
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        if "=" not in line:
            raise ValueError(f"Values line {n}: expected 'number = name'")
        key, name = line.split("=", 1)
        values[parse_int(key.strip())] = name.strip()
    return values


def _values_to_text(values: dict[int, str]) -> str:
    return "\n".join(f"0x{k:02X} = {v}" for k, v in sorted(values.items()))


class FieldEditDialog(QDialog):
    """Edit a single field definition."""

    def __init__(self, field_def: FieldDefinition, other_fields: list[str], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Edit Field - {field_def.name or 'new'}")
        self.resize(640, 680)
        self.other_fields = other_fields
        self.result_field: FieldDefinition | None = None
        self._extra = dict(field_def.extra)

        root = QVBoxLayout(self)
        tabs = QTabWidget()
        root.addWidget(tabs)

        # --- General -------------------------------------------------------
        general = QWidget()
        form = QFormLayout(general)
        self.name = QLineEdit(field_def.name)
        self.type = QComboBox()
        for t in FIELD_TYPES:
            self.type.addItem(type_label(t), t)
        self.type.setCurrentIndex(max(0, self.type.findData(field_def.type)))
        self.description = QLineEdit(field_def.description)

        self.offset_mode = QComboBox()
        for label, key in OFFSET_MODES:
            self.offset_mode.addItem(label, key)
        self.offset_value = QSpinBox()
        self.offset_value.setRange(-65535, 65535)
        if field_def.offset == "auto":
            self.offset_mode.setCurrentIndex(0)
        elif field_def.offset == "last":
            self.offset_mode.setCurrentIndex(2)
        elif isinstance(field_def.offset, int) and field_def.offset < 0:
            self.offset_mode.setCurrentIndex(3)
            self.offset_value.setValue(field_def.offset)
        else:
            self.offset_mode.setCurrentIndex(1)
            self.offset_value.setValue(int(field_def.offset))
        offset_row = QHBoxLayout()
        offset_row.addWidget(self.offset_mode, 1)
        offset_row.addWidget(self.offset_value)

        self.length_mode = QComboBox()
        for label, key in LENGTH_MODES:
            self.length_mode.addItem(label, key)
        self.length_value = QSpinBox()
        self.length_value.setRange(0, 65535)
        self.length_from = QComboBox()
        self.length_from.addItems(other_fields)
        self.length_scale = QSpinBox()
        self.length_scale.setRange(1, 1024)
        self.length_scale.setValue(field_def.length_scale)
        self.length_scale.setPrefix("× ")
        self.length_adjust = QSpinBox()
        self.length_adjust.setRange(-65535, 65535)
        self.length_adjust.setValue(field_def.length_adjust)
        self.length_adjust.setPrefix("+ ")
        self.length_adjust.setToolTip("Added to the length field value (e.g. -2 if the length includes a 2-byte header)")
        if field_def.length_from:
            self.length_mode.setCurrentIndex(1)
            idx = self.length_from.findText(field_def.length_from)
            if idx >= 0:
                self.length_from.setCurrentIndex(idx)
        elif field_def.length == "remaining":
            self.length_mode.setCurrentIndex(2)
        else:
            self.length_mode.setCurrentIndex(0)
            size = field_def.fixed_size()
            self.length_value.setValue(size if size is not None else 1)
        length_row = QHBoxLayout()
        for w in (self.length_mode, self.length_value, self.length_from, self.length_scale, self.length_adjust):
            length_row.addWidget(w)

        self.endian = QComboBox()
        self.endian.addItem("Protocol default", None)
        self.endian.addItem("Little endian", "little")
        self.endian.addItem("Big endian", "big")
        self.endian.setCurrentIndex(max(0, self.endian.findData(field_def.endian)))
        self.encoding = QLineEdit(field_def.encoding or "")
        self.encoding.setPlaceholderText("default (ascii / utf-8) - e.g. latin-1, cp1252")
        self.null_terminated = QCheckBox("Stop at first 0x00")
        self.null_terminated.setChecked(field_def.null_terminated)
        self.expected = QLineEdit(format_hex(field_def.expected) if field_def.expected else "")
        self.expected.setPlaceholderText("optional, e.g. 02 - decode reports a mismatch")

        form.addRow("Name:", self.name)
        form.addRow("Type:", self.type)
        form.addRow("Offset:", offset_row)
        form.addRow("Length:", length_row)
        form.addRow("Endianness:", self.endian)
        form.addRow("Text encoding:", self.encoding)
        form.addRow("", self.null_terminated)
        form.addRow("Expected bytes:", self.expected)
        form.addRow("Description:", self.description)

        self.scale = QLineEdit("" if field_def.scale is None else repr(field_def.scale))
        self.scale.setPlaceholderText("e.g. 0.1")
        self.bias = QLineEdit("" if field_def.bias is None else repr(field_def.bias))
        self.bias.setPlaceholderText("e.g. -40")
        self.unit = QLineEdit(field_def.unit)
        self.unit.setPlaceholderText("e.g. °C")
        scale_row = QHBoxLayout()
        for label, w in (("Scale", self.scale), ("Bias", self.bias), ("Unit", self.unit)):
            scale_row.addWidget(QLabel(label))
            scale_row.addWidget(w)
        form.addRow("Engineering value:", scale_row)
        tabs.addTab(general, "General")

        # --- Values / bits ---------------------------------------------------
        vb = QWidget()
        vbl = QVBoxLayout(vb)
        vbl.addWidget(QLabel("Value names (one per line:  0x01 = Read Coils)"))
        self.values = QPlainTextEdit(_values_to_text(field_def.values))
        self.values.setFont(theme.mono_font(10))
        self.values.setMaximumHeight(110)
        vbl.addWidget(self.values)
        vbl.addWidget(QLabel("Bit fields (bit 0 = least significant bit). Value names: 0=Off;1=On"))
        self.bits = QTableWidget(0, 5)
        self.bits.setHorizontalHeaderLabels(["Bit Name", "Start Bit", "Bit Length", "Description", "Value names"])
        self.bits.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.bits.verticalHeader().setVisible(False)
        for b in field_def.bits:
            self._add_bit_row(b)
        vbl.addWidget(self.bits)
        bit_buttons = QHBoxLayout()
        add_bit = QPushButton("Add Bit")
        add_bit.clicked.connect(lambda: self._add_bit_row(BitDefinition(f"bit{self.bits.rowCount()}",
                                                                        self.bits.rowCount())))
        del_bit = QPushButton("Delete Bit")
        del_bit.clicked.connect(lambda: self.bits.removeRow(self.bits.currentRow()))
        bit_buttons.addWidget(add_bit)
        bit_buttons.addWidget(del_bit)
        bit_buttons.addStretch()
        vbl.addLayout(bit_buttons)
        tabs.addTab(vb, "Values && Bits")

        # --- CRC / checksum ---------------------------------------------------
        chk = QWidget()
        cl = QVBoxLayout(chk)
        self.check_group = QGroupBox("CRC / checksum (only for CRC and Checksum field types)")
        cf = QFormLayout(self.check_group)
        self.algorithm = QComboBox()
        self.width = QSpinBox()
        self.width.setRange(8, 64)
        self.poly = QLineEdit()
        self.init = QLineEdit()
        self.xorout = QLineEdit()
        self.refin = QCheckBox("Reflect input")
        self.refout = QCheckBox("Reflect output")
        self.range_start = QComboBox()
        self.range_end = QComboBox()
        for combo, default in ((self.range_start, "(packet start)"), (self.range_end, "(byte before this field)")):
            combo.setEditable(True)
            combo.addItem(default)
            combo.addItems(other_fields)
            combo.setToolTip("Field name, or a byte offset (negative = from end)")
        self.crc_desc = QLabel()
        self.crc_desc.setWordWrap(True)
        cf.addRow("Algorithm:", self.algorithm)
        cf.addRow("Width (bits):", self.width)
        cf.addRow("Polynomial:", self.poly)
        cf.addRow("Initial value:", self.init)
        cf.addRow("Final XOR:", self.xorout)
        cf.addRow("", self.refin)
        cf.addRow("", self.refout)
        cf.addRow("Range start:", self.range_start)
        cf.addRow("Range end (incl.):", self.range_end)
        cf.addRow("", self.crc_desc)
        cl.addWidget(self.check_group)
        cl.addStretch()
        tabs.addTab(chk, "CRC / Checksum")

        self._initial_check = field_def.check
        self.type.currentIndexChanged.connect(self._on_type_changed)
        self.algorithm.currentIndexChanged.connect(self._on_algorithm_changed)
        self.offset_mode.currentIndexChanged.connect(self._update_enabled)
        self.length_mode.currentIndexChanged.connect(self._update_enabled)
        self._on_type_changed(initial=True)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # ---------------------------------------------------------- helpers
    def _add_bit_row(self, b: BitDefinition) -> None:
        row = self.bits.rowCount()
        self.bits.insertRow(row)
        names = ";".join(f"{k}={v}" for k, v in sorted(b.values.items()))
        for col, text in enumerate((b.name, str(b.start), str(b.length), b.description, names)):
            self.bits.setItem(row, col, QTableWidgetItem(text))

    def _on_type_changed(self, *_args, initial: bool = False) -> None:
        t = self.type.currentData()
        self.algorithm.blockSignals(True)
        self.algorithm.clear()
        if t == "crc":
            for name in crc_mod.preset_names():
                self.algorithm.addItem(name, name)
            self.algorithm.addItem("custom", "custom")
        elif t == "checksum":
            for name in checksum_mod.checksum_names():
                self.algorithm.addItem(name, name)
        self.algorithm.blockSignals(False)

        if t in ("crc", "checksum"):
            cfg = self._initial_check if initial and self._initial_check else None
            alg = cfg.algorithm if cfg else ("CRC-16" if t == "crc" else "sum8")
            idx = self.algorithm.findData(alg)
            if idx < 0 and t == "crc":
                try:
                    idx = self.algorithm.findData(crc_mod.get_preset(alg).name)
                except crc_mod.CRCError:
                    idx = self.algorithm.findData("custom")
            self.algorithm.setCurrentIndex(max(0, idx))
            self._on_algorithm_changed()
            if cfg:
                for combo, ref in ((self.range_start, cfg.range_start), (self.range_end, cfg.range_end)):
                    if ref is not None:
                        combo.setCurrentText(str(ref))
                if t == "crc" and cfg.params:
                    try:
                        self._show_crc(cfg.crc_params())
                    except crc_mod.CRCError:
                        pass
        if t in TYPE_SIZES and self.length_mode.currentData() == "fixed":
            self.length_value.setValue(TYPE_SIZES[t])
        self._update_enabled()

    def _on_algorithm_changed(self) -> None:
        t = self.type.currentData()
        alg = self.algorithm.currentData()
        if t == "crc" and alg and alg != "custom":
            params = crc_mod.get_preset(alg)
            self._show_crc(params)
            if self.length_mode.currentData() == "fixed":
                self.length_value.setValue(params.byte_length)
        elif t == "checksum" and alg:
            a = checksum_mod.get_checksum(alg)
            self.crc_desc.setText(a.description)
            if self.length_mode.currentData() == "fixed":
                self.length_value.setValue(a.byte_length)
        self._update_enabled()

    def _show_crc(self, p: crc_mod.CRCParams) -> None:
        digits = (p.width + 3) // 4
        self.width.setValue(p.width)
        self.poly.setText(f"0x{p.poly:0{digits}X}")
        self.init.setText(f"0x{p.init:0{digits}X}")
        self.xorout.setText(f"0x{p.xorout:0{digits}X}")
        self.refin.setChecked(p.refin)
        self.refout.setChecked(p.refout)
        self.crc_desc.setText(p.note or p.describe())

    def _update_enabled(self) -> None:
        t = self.type.currentData()
        is_crc = t == "crc"
        self.check_group.setEnabled(t in ("crc", "checksum"))
        for w in (self.width, self.poly, self.init, self.xorout, self.refin, self.refout):
            w.setEnabled(is_crc)
        self.offset_value.setEnabled(self.offset_mode.currentData() in ("abs", "end"))
        mode = self.length_mode.currentData()
        self.length_value.setEnabled(mode == "fixed" and t not in TYPE_SIZES)
        for w in (self.length_from, self.length_scale, self.length_adjust):
            w.setEnabled(mode == "from")
        text_type = t in ("ascii", "utf8")
        self.encoding.setEnabled(text_type)
        self.null_terminated.setEnabled(text_type)
        numeric = t in TYPE_SIZES
        for w in (self.scale, self.bias, self.unit):
            w.setEnabled(numeric)

    @staticmethod
    def _range_value(combo: QComboBox) -> str | int | None:
        text = combo.currentText().strip()
        if not text or text.startswith("("):
            return None
        try:
            return int(text, 0)
        except ValueError:
            return text

    def _build(self) -> FieldDefinition:
        name = self.name.text().strip()
        if not name:
            raise ValueError("Field name is required")
        t = self.type.currentData()
        mode = self.offset_mode.currentData()
        offset: int | str = {"auto": "auto", "last": "last"}.get(mode, self.offset_value.value())
        if mode == "end" and self.offset_value.value() >= 0:
            raise ValueError("'From end' offset must be negative (-1 = last byte)")
        if mode == "abs" and self.offset_value.value() < 0:
            raise ValueError("Absolute offset must be >= 0")

        lmode = self.length_mode.currentData()
        length: int | str | None = None
        length_from = None
        if lmode == "fixed":
            length = TYPE_SIZES.get(t, self.length_value.value())
        elif lmode == "remaining":
            length = "remaining"
        else:
            length_from = self.length_from.currentText() or None
            if not length_from:
                raise ValueError("Select the field that holds the length")

        values = _parse_values_text(self.values.toPlainText())
        bits = []
        for row in range(self.bits.rowCount()):
            cells = [(self.bits.item(row, c).text() if self.bits.item(row, c) else "") for c in range(5)]
            bit_values = {}
            for part in filter(None, (p.strip() for p in cells[4].split(";"))):
                if "=" not in part:
                    raise ValueError(f"Bit '{cells[0]}': value names must look like 0=Off;1=On")
                k, v = part.split("=", 1)
                bit_values[parse_int(k.strip())] = v.strip()
            bits.append(BitDefinition(cells[0].strip() or f"bit{cells[1]}", int(parse_int(cells[1] or "0")),
                                      int(parse_int(cells[2] or "1")), cells[3], bit_values))

        check = None
        if t in ("crc", "checksum"):
            alg = self.algorithm.currentData() or ("CRC-16" if t == "crc" else "sum8")
            params = {}
            if t == "crc":
                custom = {"width": self.width.value(), "poly": self.poly.text().strip(),
                          "init": self.init.text().strip() or "0", "xorout": self.xorout.text().strip() or "0",
                          "refin": self.refin.isChecked(), "refout": self.refout.isChecked()}
                if alg == "custom":
                    params = custom
                else:
                    preset = crc_mod.get_preset(alg)
                    resolved = crc_mod.resolve_crc({"algorithm": "custom", **custom})
                    if (resolved.width, resolved.poly, resolved.init, resolved.xorout, resolved.refin,
                            resolved.refout) != (preset.width, preset.poly, preset.init, preset.xorout,
                                                 preset.refin, preset.refout):
                        alg, params = "custom", custom
            check = CheckConfig(alg, self._range_value(self.range_start), self._range_value(self.range_end), params)
            if t == "crc":
                size = check.crc_params().byte_length
            else:
                size = checksum_mod.get_checksum(alg).byte_length
            if lmode == "fixed":
                length = size

        def opt_float(edit: QLineEdit) -> float | None:
            text = edit.text().strip()
            return float(text) if text else None

        try:
            expected = parse_hex_bytes(self.expected.text().strip() or None)
        except (HexParseError, ValueError) as exc:
            raise ValueError(f"Expected bytes: {exc}") from exc

        return FieldDefinition(
            name=name, type=t, offset=offset, length=length, length_from=length_from,
            length_scale=self.length_scale.value() if length_from else 1,
            length_adjust=self.length_adjust.value() if length_from else 0,
            endian=self.endian.currentData(), encoding=self.encoding.text().strip() or None,
            description=self.description.text().strip(), expected=expected, values=values, bits=bits,
            scale=opt_float(self.scale), bias=opt_float(self.bias), unit=self.unit.text().strip(),
            null_terminated=self.null_terminated.isChecked(), check=check, extra=self._extra,
        )

    def _accept(self) -> None:
        try:
            self.result_field = self._build()
        except (ValueError, crc_mod.CRCError, ProtocolError) as exc:
            QMessageBox.warning(self, "Invalid field", str(exc))
            return
        self.accept()


class ProtocolEditorDialog(QDialog):
    """Create / edit a protocol definition and save it as JSON."""

    COLUMNS = ["Name", "Offset", "Length", "Type", "Endian", "Description"]

    def __init__(self, protocol: ProtocolDefinition, manager: ProtocolManager, is_new: bool = False,
                 parent=None) -> None:
        super().__init__(parent)
        self.manager = manager
        self.protocol = protocol.clone()
        self.original_name = None if is_new else protocol.name
        self.is_new = is_new
        self.saved_protocol: ProtocolDefinition | None = None
        self.setWindowTitle("Protocol Editor")
        self.resize(980, 640)

        root = QVBoxLayout(self)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs)

        # --- Definition tab --------------------------------------------------
        page = QWidget()
        pl = QVBoxLayout(page)
        form = QFormLayout()
        self.name = QLineEdit()
        self.description = QLineEdit()
        self.default_endian = QComboBox()
        self.default_endian.addItem("Big endian", "big")
        self.default_endian.addItem("Little endian", "little")
        self.start_byte = QLineEdit()
        self.start_byte.setPlaceholderText("optional, e.g. 02 - reports 'Missing STX'")
        self.end_byte = QLineEdit()
        self.end_byte.setPlaceholderText("optional, e.g. 03 - reports 'Missing ETX'")
        frame_row = QHBoxLayout()
        frame_row.addWidget(QLabel("Start byte(s):"))
        frame_row.addWidget(self.start_byte)
        frame_row.addWidget(QLabel("End byte(s):"))
        frame_row.addWidget(self.end_byte)
        form.addRow("Protocol Name:", self.name)
        form.addRow("Description:", self.description)
        form.addRow("Default endianness:", self.default_endian)
        form.addRow("Frame:", frame_row)
        pl.addLayout(form)

        pl.addWidget(QLabel("Fields:"))
        body = QHBoxLayout()
        self.table = CopyableTable(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.doubleClicked.connect(self.edit_field)
        body.addWidget(self.table, 1)

        side = QVBoxLayout()
        for label, slot, tip in (
            ("Add Field", self.add_field, "Insert a new field after the selected one"),
            ("Edit Field", self.edit_field, "Edit the selected field (double-click also works)"),
            ("Delete Field", self.delete_field, "Remove the selected field"),
            ("Move Up", lambda: self.move_field(-1), "Move the selected field up"),
            ("Move Down", lambda: self.move_field(1), "Move the selected field down"),
        ):
            b = QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            side.addWidget(b)
        side.addSpacing(16)
        validate = QPushButton("Validate")
        validate.setToolTip("Check the definition for errors")
        validate.clicked.connect(self.show_validation)
        side.addWidget(validate)
        side.addStretch()
        body.addLayout(side)
        pl.addLayout(body, 1)
        self.issues = QLabel()
        self.issues.setWordWrap(True)
        pl.addWidget(self.issues)
        self.tabs.addTab(page, "Definition")

        # --- JSON tab ---------------------------------------------------------
        json_page = QWidget()
        jl = QVBoxLayout(json_page)
        jl.addWidget(QLabel("Raw JSON (advanced). Click 'Apply JSON' to load your edits into the editor."))
        self.json_edit = QPlainTextEdit()
        self.json_edit.setFont(theme.mono_font(10))
        jl.addWidget(self.json_edit, 1)
        apply_json = QPushButton("Apply JSON")
        apply_json.clicked.connect(self.apply_json)
        jl.addWidget(apply_json, 0, Qt.AlignmentFlag.AlignLeft)
        self.tabs.addTab(json_page, "JSON")
        self.tabs.currentChanged.connect(self._on_tab_changed)

        # --- bottom buttons ---------------------------------------------------
        bottom = QHBoxLayout()
        load_btn = QPushButton("Load Protocol…")
        load_btn.setToolTip("Load a protocol JSON file into the editor")
        load_btn.clicked.connect(self.load_protocol)
        save_as_btn = QPushButton("Save As…")
        save_as_btn.clicked.connect(lambda: self.save_protocol(ask_path=True))
        save_btn = QPushButton("Save Protocol")
        save_btn.setObjectName("Primary")
        save_btn.setToolTip("Save the protocol as JSON and use it")
        save_btn.clicked.connect(lambda: self.save_protocol(ask_path=False))
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        bottom.addWidget(load_btn)
        bottom.addStretch()
        self.path_label = QLabel()
        bottom.addWidget(self.path_label)
        bottom.addWidget(save_as_btn)
        bottom.addWidget(save_btn)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

        self._load_into_widgets()

    # ----------------------------------------------------------- binding
    def _load_into_widgets(self) -> None:
        p = self.protocol
        self.name.setText(p.name)
        self.description.setText(p.description)
        self.default_endian.setCurrentIndex(max(0, self.default_endian.findData(p.default_endian)))
        self.start_byte.setText(format_hex(p.frame.start_bytes) if p.frame.start_bytes else "")
        self.end_byte.setText(format_hex(p.frame.end_bytes) if p.frame.end_bytes else "")
        self.path_label.setText(Path(p.source_path).name if p.source_path else "(not saved)")
        self._refresh_table()

    def _collect_header(self) -> None:
        p = self.protocol
        p.name = self.name.text().strip()
        p.description = self.description.text().strip()
        p.default_endian = self.default_endian.currentData()
        try:
            p.frame = FrameSpec(parse_hex_bytes(self.start_byte.text().strip() or None),
                                parse_hex_bytes(self.end_byte.text().strip() or None))
        except (HexParseError, ValueError) as exc:
            raise ValueError(f"Invalid frame start/end byte: {exc}") from exc

    def _refresh_table(self, select: int | None = None) -> None:
        self.table.setRowCount(0)
        for f in self.protocol.fields:
            row = self.table.rowCount()
            self.table.insertRow(row)
            endian = f.endian or f"({self.protocol.default_endian})"
            if f.type in ("ascii", "utf8", "hex", "binary", "uint8", "int8"):
                endian = "-"
            type_text = type_label(f.type)
            if f.check:
                type_text += f" ({f.check.algorithm})"
            for col, text in enumerate((f.name, f.offset_text(), f.length_text(), type_text, endian, f.description)):
                self.table.setItem(row, col, make_item(text))
        if select is not None and 0 <= select < self.table.rowCount():
            self.table.selectRow(select)
        self._show_issues()

    def _show_issues(self) -> None:
        issues = self.protocol.validate()
        c = theme.colors()
        if not issues:
            self.issues.setText(f'<span style="color:{c.ok}">✓ Definition is valid</span>')
        else:
            parts = [f'<span style="color:{c.error if i.severity == "error" else c.warn}">'
                     f'{"✗" if i.severity == "error" else "⚠"} {i.message}</span>' for i in issues[:6]]
            if len(issues) > 6:
                parts.append(f"… {len(issues) - 6} more")
            self.issues.setText("<br>".join(parts))

    def _selected(self) -> int:
        rows = {i.row() for i in self.table.selectedIndexes()}
        return min(rows) if rows else -1

    # ------------------------------------------------------------ fields
    def add_field(self) -> None:
        at = self._selected()
        at = len(self.protocol.fields) if at < 0 else at + 1
        names = [f.name for f in self.protocol.fields[:at]]
        base, n = "Field", len(self.protocol.fields) + 1
        while any(f.name == f"{base}{n}" for f in self.protocol.fields):
            n += 1
        dlg = FieldEditDialog(FieldDefinition(f"{base}{n}", "uint8"), names, self)
        if dlg.exec() and dlg.result_field:
            self.protocol.fields.insert(at, dlg.result_field)
            self._refresh_table(at)

    def edit_field(self, *_args) -> None:
        idx = self._selected()
        if idx < 0:
            return
        names = [f.name for f in self.protocol.fields if f is not self.protocol.fields[idx]]
        old = self.protocol.fields[idx]
        dlg = FieldEditDialog(old, names, self)
        if dlg.exec() and dlg.result_field:
            new = dlg.result_field
            if new.name != old.name:
                self._rename_references(old.name, new.name)
            self.protocol.fields[idx] = new
            self._refresh_table(idx)

    def _rename_references(self, old: str, new: str) -> None:
        for f in self.protocol.fields:
            if f.length_from == old:
                f.length_from = new
            if f.check:
                if f.check.range_start == old:
                    f.check.range_start = new
                if f.check.range_end == old:
                    f.check.range_end = new

    def delete_field(self) -> None:
        idx = self._selected()
        if idx < 0:
            return
        name = self.protocol.fields[idx].name
        if QMessageBox.question(self, "Delete field", f"Delete field '{name}'?") != QMessageBox.StandardButton.Yes:
            return
        del self.protocol.fields[idx]
        self._refresh_table(min(idx, len(self.protocol.fields) - 1))

    def move_field(self, delta: int) -> None:
        idx = self._selected()
        new = idx + delta
        if idx < 0 or not 0 <= new < len(self.protocol.fields):
            return
        f = self.protocol.fields
        f[idx], f[new] = f[new], f[idx]
        self._refresh_table(new)

    def show_validation(self) -> None:
        try:
            self._collect_header()
        except ValueError as exc:
            QMessageBox.warning(self, "Validate", str(exc))
            return
        issues = self.protocol.validate()
        self._show_issues()
        if not issues:
            QMessageBox.information(self, "Validate", "✓ The protocol definition is valid.")
        else:
            QMessageBox.warning(self, "Validate", "\n".join(str(i) for i in issues))

    # -------------------------------------------------------------- JSON
    def _on_tab_changed(self, index: int) -> None:
        if self.tabs.widget(index) is not None and index == 1:
            try:
                self._collect_header()
            except ValueError:
                pass
            self.json_edit.setPlainText(json.dumps(self.protocol.to_dict(), indent=4, ensure_ascii=False))

    def apply_json(self) -> None:
        try:
            data = json.loads(self.json_edit.toPlainText())
            proto = ProtocolDefinition.from_dict(data)
        except json.JSONDecodeError as exc:
            QMessageBox.warning(self, "Invalid JSON", f"Line {exc.lineno}, column {exc.colno}: {exc.msg}")
            return
        except ProtocolError as exc:
            QMessageBox.warning(self, "Invalid protocol definition", str(exc))
            return
        proto.source_path = self.protocol.source_path
        self.protocol = proto
        self._load_into_widgets()
        self.tabs.setCurrentIndex(0)

    # ------------------------------------------------------------- files
    def load_protocol(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load protocol", str(self.manager.user_dir),
                                              "Protocol JSON (*.json);;All files (*)")
        if not path:
            return
        try:
            self.protocol = self.manager.load_file(path)
        except (ProtocolError, ValueError, OSError) as exc:
            QMessageBox.warning(self, "Load protocol", str(exc))
            return
        self.original_name = None
        self._load_into_widgets()

    def save_protocol(self, ask_path: bool) -> None:
        if self.tabs.currentIndex() == 1:
            self.apply_json()
        try:
            self._collect_header()
        except ValueError as exc:
            QMessageBox.warning(self, "Save protocol", str(exc))
            return
        errors = self.protocol.errors()
        if errors:
            answer = QMessageBox.question(
                self, "Save protocol", "The definition has errors and cannot decode packets yet:\n\n"
                + "\n".join(errors[:8]) + "\n\nSave anyway?")
            if answer != QMessageBox.StandardButton.Yes:
                return
        existing = self.manager.get(self.protocol.name)
        if existing is not None and self.protocol.name != self.original_name and not ask_path:
            answer = QMessageBox.question(self, "Save protocol",
                                          f"A protocol named '{self.protocol.name}' already exists. Overwrite it?")
            if answer != QMessageBox.StandardButton.Yes:
                return
            self.protocol.source_path = existing.source_path
        path = None
        if ask_path:
            start = self.protocol.source_path or str(self.manager.new_protocol_path(self.protocol.name))
            path, _ = QFileDialog.getSaveFileName(self, "Save protocol as", start, "Protocol JSON (*.json)")
            if not path:
                return
        elif self.is_new and not self.protocol.source_path:
            path = self.manager.new_protocol_path(self.protocol.name)
        try:
            saved = self.manager.save(self.protocol, path, previous_name=self.original_name)
        except OSError as exc:
            QMessageBox.warning(self, "Save protocol", f"Cannot save: {exc}")
            return
        self.original_name = self.protocol.name
        self.is_new = False
        self.saved_protocol = self.protocol.clone()
        self.path_label.setText(saved.name)
        self.accept()
