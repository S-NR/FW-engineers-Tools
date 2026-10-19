"""Deterministic packet decoder - the single source of truth for decoded data.

``decode_packet(protocol, data)`` never raises: every problem (malformed
packet, bad definition, internal error) is reported in the result.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core import checksum as checksum_mod
from core import crc as crc_mod
from core.field_decoder import UNDEFINED, DecodedBit, decode_field_value
from core.packet_parser import FieldSlice, Layout, resolve_layout
from core.protocol import FieldDefinition, ProtocolDefinition, type_label
from utils.hex_utils import format_hex

__all__ = [
    "STATUS_VALID",
    "STATUS_WARNING",
    "STATUS_CHECK_ERROR",
    "STATUS_INVALID",
    "CheckResult",
    "DecodedField",
    "DecodeResult",
    "decode_packet",
]

STATUS_VALID = "VALID"
STATUS_WARNING = "VALID (WARNINGS)"
STATUS_CHECK_ERROR = "CRC ERROR"
STATUS_INVALID = "INVALID"


@dataclass
class CheckResult:
    kind: str  # "CRC" | "Checksum"
    algorithm: str
    received: bytes
    calculated: bytes | None
    range_start: int
    range_end: int  # inclusive
    error: str = ""

    @property
    def valid(self) -> bool:
        return self.calculated is not None and self.received == self.calculated

    def summary_lines(self) -> list[str]:
        lines = [
            f"{self.kind} algorithm : {self.algorithm}",
            f"{self.kind} range     : offsets {self.range_start}-{self.range_end}",
            f"{self.kind} Received  : {format_hex(self.received) or '(missing)'}",
            f"{self.kind} Calculated: {format_hex(self.calculated) if self.calculated is not None else '-'}",
        ]
        if self.error:
            lines.append(f"✗ {self.error}")
        else:
            lines.append(f"✓ {self.kind.upper()} VALID" if self.valid else f"✗ {self.kind.upper()} INVALID")
        return lines


@dataclass
class DecodedField:
    name: str
    field_type: str
    offset: int
    length: int
    raw: bytes
    value: Any = None
    display: str = ""
    interpretation: str = ""
    description: str = ""
    status: str = "ok"  # ok | warning | error | undefined
    messages: list[str] = field(default_factory=list)
    bits: list[DecodedBit] = field(default_factory=list)
    check: CheckResult | None = None
    defined: bool = True
    index: int = -1  # index in protocol.fields, -1 for undefined regions

    @property
    def end(self) -> int:
        return self.offset + self.length

    @property
    def offset_text(self) -> str:
        if self.length <= 1:
            return str(self.offset)
        return f"{self.offset}-{self.offset + self.length - 1}"

    @property
    def type_text(self) -> str:
        return type_label(self.field_type) if self.defined else "-"


@dataclass
class DecodeResult:
    protocol_name: str
    data: bytes
    fields: list[DecodedField] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: list[CheckResult] = field(default_factory=list)
    status: str = STATUS_VALID

    @property
    def ok(self) -> bool:
        return not self.errors

    def field(self, name: str) -> DecodedField | None:
        for f in self.fields:
            if f.name == name and f.defined:
                return f
        return None

    def value(self, name: str) -> Any:
        f = self.field(name)
        return f.value if f else None

    def status_message(self) -> str:
        if not self.errors and not self.warnings:
            return "✓ Packet valid"
        if not self.errors:
            return f"⚠ Packet valid with {len(self.warnings)} warning(s): {self.warnings[0]}"
        return f"✗ {self.errors[0]}" + (f" (+{len(self.errors) - 1} more)" if len(self.errors) > 1 else "")

    def to_text(self) -> str:
        """Plain-text report (used by Copy and the AI prompt builder)."""
        lines = [f"Protocol: {self.protocol_name}", f"Packet ({len(self.data)} bytes): {format_hex(self.data)}",
                 f"Status: {self.status}", "",
                 f"{'Offset':<8}{'Bytes':<26}{'Field':<16}{'Value':<24}Interpretation"]
        for f in self.fields:
            raw = format_hex(f.raw)
            if len(raw) > 24:
                raw = raw[:21] + "..."
            lines.append(f"{f.offset_text:<8}{raw:<26}{f.name:<16}{f.display:<24}{f.interpretation}")
            for b in f.bits:
                lines.append(f"{'':<8}{'':<26}  {b.bit_label} {b.name:<10} = {b.value}  {b.interpretation}")
        for c in self.checks:
            lines.append("")
            lines.extend(c.summary_lines())
        if self.errors:
            lines.append("")
            lines.extend(f"ERROR: {e}" for e in self.errors)
        if self.warnings:
            lines.append("")
            lines.extend(f"WARNING: {w}" for w in self.warnings)
        return "\n".join(lines)


# ------------------------------------------------------------------ decode


def decode_packet(protocol: ProtocolDefinition, data: bytes) -> DecodeResult:
    data = bytes(data)
    result = DecodeResult(protocol.name if protocol else "(none)", data)
    try:
        _decode(protocol, data, result)
    except Exception as exc:  # last line of defence - report, never crash
        result.errors.append(f"Internal decoder error: {type(exc).__name__}: {exc}")
    _finalise_status(result)
    return result


def _decode(protocol: ProtocolDefinition, data: bytes, result: DecodeResult) -> None:
    if protocol is None:
        result.errors.append("No protocol selected")
        return
    if not data:
        result.errors.append("Empty packet")
        return

    definition_errors = protocol.errors()
    if definition_errors:
        result.errors.extend(f"Invalid protocol definition: {e}" for e in definition_errors)
        result.fields.append(_undefined(data, 0, len(data)))
        return

    _check_frame(protocol, data, result)
    layout = resolve_layout(protocol, data)

    for idx, sl in enumerate(layout.slices):
        df = _decode_slice(protocol, data, sl, idx)
        result.errors.extend(sl.errors)
        result.warnings.extend(sl.warnings)
        result.fields.append(df)

    for df, sl in zip(list(result.fields), layout.slices):
        if sl.field.is_check and sl.complete:
            check = _verify_check(sl, data, layout, protocol.default_endian)
            df.check = check
            result.checks.append(check)
            if check.error:
                df.status = "error"
                df.interpretation = check.error
                df.messages.append(check.error)
                result.errors.append(f"{sl.field.name}: {check.error}")
            elif check.valid:
                df.interpretation = f"VALID ({check.algorithm})"
            else:
                df.status = "error"
                df.interpretation = f"INVALID - calculated {format_hex(check.calculated)}"
                msg = f"{check.kind} mismatch in '{sl.field.name}': received {format_hex(check.received)}, " \
                      f"calculated {format_hex(check.calculated)}"
                df.messages.append(msg)
                result.errors.append(msg)

    _add_undefined_regions(result)


def _check_frame(protocol: ProtocolDefinition, data: bytes, result: DecodeResult) -> None:
    start, end = protocol.frame.start_bytes, protocol.frame.end_bytes
    if start and data[: len(start)] != start:
        found = format_hex(data[: len(start)])
        result.errors.append(f"Missing STX: expected {format_hex(start)} at offset 0, found {found}")
    if end and data[-len(end):] != end:
        found = format_hex(data[-len(end):])
        result.errors.append(f"Missing ETX: expected {format_hex(end)} at end of packet, found {found}")


def _decode_slice(protocol: ProtocolDefinition, data: bytes, sl: FieldSlice, idx: int) -> DecodedField:
    f = sl.field
    raw = data[sl.offset: sl.end]
    df = DecodedField(f.name, f.type, sl.offset, sl.length, raw, description=f.description, index=idx)
    df.messages.extend(sl.errors + sl.warnings)

    if not sl.complete:
        df.status = "error"
        df.display = UNDEFINED if not raw else format_hex(raw)
        df.interpretation = "Truncated / missing" if raw or sl.expected_length else ""
        return df

    endian = f.endian or protocol.default_endian
    fv = decode_field_value(f, raw, endian)
    df.value, df.display, df.bits = fv.value, fv.display, fv.bits
    df.interpretation = fv.interpretation or f.description
    df.messages.extend(fv.errors + fv.warnings)
    if fv.errors:
        df.status = "error"
        sl.errors.extend(f"'{f.name}': {e}" for e in fv.errors)
    elif fv.warnings or sl.warnings:
        df.status = "warning"
        sl.warnings.extend(f"'{f.name}': {w}" for w in fv.warnings)

    if f.expected is not None and raw != f.expected:
        msg = f"'{f.name}' expected {format_hex(f.expected)}, found {format_hex(raw)}"
        df.status = "error"
        df.messages.append(msg)
        sl.errors.append(msg)
    return df


def _resolve_ref(ref: str | int | None, layout: Layout, n: int, use_end: bool) -> int | None:
    if isinstance(ref, int):
        return n + ref if ref < 0 else ref
    sl = layout.by_name(ref) if ref else None
    if sl is None:
        return None
    return sl.end - 1 if use_end else sl.offset


def _verify_check(sl: FieldSlice, data: bytes, layout: Layout, default_endian: str) -> CheckResult:
    f: FieldDefinition = sl.field
    cfg = f.check
    n = len(data)
    received = data[sl.offset: sl.end]
    start = _resolve_ref(cfg.range_start, layout, n, use_end=False) if cfg.range_start is not None else 0
    end = _resolve_ref(cfg.range_end, layout, n, use_end=True) if cfg.range_end is not None else sl.offset - 1
    kind = "CRC" if f.type == "crc" else "Checksum"

    if start is None or end is None:
        return CheckResult(kind, cfg.algorithm, received, None, start or 0, end or 0,
                           "check range refers to a field that is not present")
    if start < 0 or end >= n or end < start:
        return CheckResult(kind, cfg.algorithm, received, None, start, end,
                           f"check range {start}-{end} is outside the packet")

    covered = data[start: end + 1]
    order = "little" if (f.endian or default_endian) == "little" else "big"
    try:
        if f.type == "crc":
            params = cfg.crc_params()
            calc = crc_mod.crc_to_bytes(crc_mod.compute_crc(covered, params), params.width, order)
            name = params.name
        else:
            alg = checksum_mod.get_checksum(cfg.algorithm)
            calc = alg.func(covered).to_bytes(alg.byte_length, order)
            name = alg.name
    except (ValueError, crc_mod.CRCError) as exc:
        return CheckResult(kind, cfg.algorithm, received, None, start, end, str(exc))
    return CheckResult(kind, name, received, calc, start, end)


def _undefined(data: bytes, start: int, end: int) -> DecodedField:
    raw = data[start:end]
    return DecodedField(
        name="(undefined)", field_type="", offset=start, length=end - start, raw=raw,
        value=None, display=UNDEFINED, interpretation="Not described by the protocol definition",
        status="undefined", defined=False,
    )


def _add_undefined_regions(result: DecodeResult) -> None:
    n = len(result.data)
    covered = bytearray(n)
    for f in result.fields:
        for i in range(max(0, f.offset), min(n, f.end)):
            covered[i] = 1
    regions = []
    i = 0
    while i < n:
        if not covered[i]:
            j = i
            while j < n and not covered[j]:
                j += 1
            regions.append((i, j))
            i = j
        else:
            i += 1
    for start, end in regions:
        count = end - start
        if end == n:
            result.warnings.append(f"Packet longer than expected: {count} trailing byte(s) at offset {start} not described by the protocol")
        else:
            result.warnings.append(f"{count} undefined byte(s) at offset {start}-{end - 1} (packet longer than the length fields imply?)")
        result.fields.append(_undefined(result.data, start, end))
    result.fields.sort(key=lambda f: (f.offset, 0 if f.defined else 1))


def _finalise_status(result: DecodeResult) -> None:
    if not result.errors:
        result.status = STATUS_WARNING if result.warnings else STATUS_VALID
        return
    check_only = all("mismatch" in e for e in result.errors)
    if check_only:
        kinds = {c.kind for c in result.checks if not c.valid}
        result.status = "CHECKSUM ERROR" if kinds == {"Checksum"} else STATUS_CHECK_ERROR
    else:
        result.status = STATUS_INVALID
