"""Decoding of a single field's bytes according to its type.

Pure functions; never raises for malformed data - problems are reported in
the returned :class:`FieldValue` messages.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from typing import Any

from core.protocol import INTEGER_TYPES, TYPE_SIZES, FieldDefinition
from utils.ascii_utils import escape_bytes
from utils.hex_utils import format_hex

__all__ = [
    "UNDEFINED",
    "DecodedBit",
    "FieldValue",
    "decode_uint",
    "decode_int",
    "decode_float",
    "decode_text",
    "to_binary_string",
    "extract_bits",
    "decode_bits",
    "decode_field_value",
]

UNDEFINED = "Unknown / Undefined"


@dataclass
class DecodedBit:
    name: str
    start: int
    length: int
    value: int
    interpretation: str = ""
    description: str = ""

    @property
    def bit_label(self) -> str:
        if self.length == 1:
            return f"bit {self.start}"
        return f"bits {self.start + self.length - 1}..{self.start}"


@dataclass
class FieldValue:
    value: Any = None
    display: str = ""
    interpretation: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    bits: list[DecodedBit] = field(default_factory=list)


# ------------------------------------------------------------- primitives


def decode_uint(raw: bytes, endian: str = "big") -> int:
    return int.from_bytes(raw, "little" if endian == "little" else "big", signed=False)


def decode_int(raw: bytes, endian: str = "big") -> int:
    return int.from_bytes(raw, "little" if endian == "little" else "big", signed=True)


def decode_float(raw: bytes, endian: str = "big") -> float:
    if len(raw) not in (4, 8):
        raise ValueError(f"float needs 4 or 8 bytes, got {len(raw)}")
    fmt = ("<" if endian == "little" else ">") + ("f" if len(raw) == 4 else "d")
    return struct.unpack(fmt, raw)[0]


def decode_text(raw: bytes, encoding: str = "ascii", null_terminated: bool = False) -> tuple[str, list[str]]:
    """Decode text; undecodable bytes are shown as ``\\xNN`` and reported."""
    warnings: list[str] = []
    if null_terminated and b"\x00" in raw:
        raw = raw[: raw.index(b"\x00")]
    codec = {"ascii": "ascii", "utf8": "utf-8"}.get(encoding, encoding)
    try:
        text = raw.decode(codec)
    except UnicodeDecodeError as exc:
        warnings.append(f"Contains bytes that are not valid {codec.upper()} (first at index {exc.start})")
        text = raw.decode(codec, errors="backslashreplace")
    except LookupError:
        warnings.append(f"Unknown text encoding '{encoding}', shown as escaped ASCII")
        return escape_bytes(raw), warnings
    if any(ord(c) < 0x20 and c not in "\r\n\t" for c in text):
        warnings.append("Contains non-printable control characters")
    return text, warnings


def to_binary_string(raw: bytes) -> str:
    return " ".join(f"{b:08b}" for b in raw)


def extract_bits(value: int, start: int, length: int) -> int:
    return (value >> start) & ((1 << length) - 1)


def decode_bits(field_def: FieldDefinition, value: int) -> list[DecodedBit]:
    result = []
    for b in sorted(field_def.bits, key=lambda x: -x.start):
        v = extract_bits(value, b.start, b.length)
        if b.values:
            interp = b.values.get(v, f"Undefined value {v}")
        elif b.length == 1:
            interp = "Set" if v else "Clear"
        else:
            interp = str(v)
        result.append(DecodedBit(b.name, b.start, b.length, v, interp, b.description))
    return result


def _quote_text(text: str) -> str:
    """Make control characters visible (\\r, \\n, \\t, \\xNN); keep other characters."""
    out = []
    for c in text:
        if c == "\n":
            out.append("\\n")
        elif c == "\r":
            out.append("\\r")
        elif c == "\t":
            out.append("\\t")
        elif ord(c) < 0x20 or ord(c) == 0x7F:
            out.append(f"\\x{ord(c):02X}")
        else:
            out.append(c)
    return "".join(out)


def _fmt_number(x: float) -> str:
    if math.isnan(x) or math.isinf(x):
        return str(x)
    if float(x).is_integer() and abs(x) < 1e15:
        return str(int(x))
    return f"{x:.7g}"


# --------------------------------------------------------------- dispatch


def decode_field_value(field_def: FieldDefinition, raw: bytes, endian: str) -> FieldValue:
    """Decode ``raw`` (already sliced to the field) according to the field type."""
    ftype = field_def.type
    out = FieldValue()
    try:
        if ftype == "hex":
            out.value = bytes(raw)
            out.display = f"0x{raw[0]:02X}" if len(raw) == 1 else format_hex(raw)
            if field_def.bits and 1 <= len(raw) <= 8:
                out.bits = decode_bits(field_def, decode_uint(raw, endian))
        elif ftype in TYPE_SIZES and ftype.startswith(("uint", "int")):
            size = TYPE_SIZES[ftype]
            if len(raw) != size:
                out.errors.append(f"{ftype} needs {size} byte(s), got {len(raw)}")
                out.display = UNDEFINED
                return out
            signed = ftype.startswith("int")
            value = decode_int(raw, endian) if signed else decode_uint(raw, endian)
            out.value = value
            _numeric_display(field_def, value, out, size)
            if field_def.bits:
                out.bits = decode_bits(field_def, decode_uint(raw, endian))
        elif ftype in ("float32", "float64"):
            size = TYPE_SIZES[ftype]
            if len(raw) != size:
                out.errors.append(f"{ftype} needs {size} bytes, got {len(raw)}")
                out.display = UNDEFINED
                return out
            value = decode_float(raw, endian)
            out.value = value
            if field_def.scale is not None or field_def.bias is not None:
                eng = value * (field_def.scale if field_def.scale is not None else 1) + (field_def.bias or 0)
                out.display = f"{_fmt_number(eng)} {field_def.unit}".strip()
                out.interpretation = f"raw {_fmt_number(value)}"
            else:
                out.display = f"{_fmt_number(value)} {field_def.unit}".strip()
            if math.isnan(value) or math.isinf(value):
                out.warnings.append(f"Float value is {value}")
        elif ftype in ("ascii", "utf8"):
            text, warns = decode_text(raw, field_def.encoding or ftype, field_def.null_terminated)
            out.value = text
            out.display = f'"{_quote_text(text)}"'
            out.warnings.extend(warns)
        elif ftype == "binary":
            out.value = bytes(raw)
            out.display = to_binary_string(raw)
        elif ftype in ("bitfield", "bool"):
            if not 1 <= len(raw) <= 8:
                out.errors.append(f"{ftype} needs 1..8 bytes, got {len(raw)}")
                out.display = UNDEFINED
                return out
            value = decode_uint(raw, endian)
            if ftype == "bool":
                out.value = value != 0
                out.display = "True" if value else "False"
                if field_def.values:
                    out.interpretation = field_def.values.get(value, "")
                if value not in (0, 1):
                    out.warnings.append(f"Boolean raw value is 0x{value:X} (not 0/1), treated as True")
            else:
                out.value = value
                width = len(raw) * 8
                out.display = f"0x{value:0{len(raw) * 2}X} ({value:0{width}b}b)"
                out.bits = decode_bits(field_def, value)
                if not field_def.bits:
                    out.interpretation = "No bit definitions - meaning undefined"
        elif ftype in ("crc", "checksum"):
            out.value = decode_uint(raw, endian)
            out.display = f"0x{out.value:0{max(1, len(raw)) * 2}X}"
        else:
            out.errors.append(f"Unsupported field type '{ftype}'")
            out.display = UNDEFINED
    except Exception as exc:  # defensive: a field must never crash the decoder
        out.errors.append(f"Cannot decode field: {exc}")
        out.display = UNDEFINED
    return out


def _numeric_display(field_def: FieldDefinition, value: int, out: FieldValue, size: int) -> None:
    if field_def.scale is not None or field_def.bias is not None:
        eng = value * (field_def.scale if field_def.scale is not None else 1) + (field_def.bias or 0)
        out.display = f"{_fmt_number(eng)} {field_def.unit}".strip()
        out.interpretation = f"raw {value} (0x{value & ((1 << size * 8) - 1):0{size * 2}X})"
    else:
        out.display = f"{value} {field_def.unit}".strip()
        if value < 0:
            out.display += f" (0x{value & ((1 << size * 8) - 1):0{size * 2}X})"
        elif value > 9:
            out.display += f" (0x{value:0{size * 2}X})"
    if field_def.values:
        name = field_def.values.get(value)
        out.interpretation = name if name is not None else f"Undefined value {value} (not in definition)"


def is_integer_type(ftype: str) -> bool:
    return ftype in INTEGER_TYPES
