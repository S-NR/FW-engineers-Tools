"""Protocol definition model (pure data, no GUI).

A protocol is a list of fields laid out over a packet. Field placement is
described declaratively so variable-length frames can be decoded without code:

``offset``
    ``"auto"`` (default) - directly after the previous field;
    an integer >= 0 - absolute byte offset;
    a negative integer - counted from the end of the packet (-1 = last byte);
    ``"last"`` - the field ends at the last byte of the packet.

``length``
    an integer - fixed number of bytes (defaults to the type size for numeric
    types); ``"remaining"`` - everything up to the fixed-size fields that follow;
    or use ``length_from`` to take the length from an earlier integer field
    (``length = value * length_scale + length_adjust``).

Unknown JSON keys are preserved in ``extra`` so newer files survive a
load/save round-trip in an older version of the tool.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from core import checksum as checksum_mod
from core import crc as crc_mod
from utils.hex_utils import HexParseError, format_hex, parse_hex_bytes, parse_int

__all__ = [
    "SCHEMA_VERSION",
    "ProtocolError",
    "FIELD_TYPES",
    "TYPE_SIZES",
    "INTEGER_TYPES",
    "TEXT_TYPES",
    "VARIABLE_TYPES",
    "normalize_type",
    "BitDefinition",
    "CheckConfig",
    "FieldDefinition",
    "FrameSpec",
    "ExamplePacket",
    "ProtocolIssue",
    "ProtocolDefinition",
]

SCHEMA_VERSION = 1


class ProtocolError(ValueError):
    """Invalid protocol definition."""


TYPE_SIZES: dict[str, int] = {
    "uint8": 1, "uint16": 2, "uint32": 4, "uint64": 8,
    "int8": 1, "int16": 2, "int32": 4, "int64": 8,
    "float32": 4, "float64": 8,
}
INTEGER_TYPES = {"uint8", "uint16", "uint32", "uint64", "int8", "int16", "int32", "int64", "bitfield", "bool"}
TEXT_TYPES = {"ascii", "utf8"}
VARIABLE_TYPES = {"hex", "ascii", "utf8", "binary"}
CHECK_TYPES = {"crc", "checksum"}

FIELD_TYPES: list[str] = [
    "hex", "uint8", "uint16", "uint32", "uint64", "int8", "int16", "int32", "int64",
    "float32", "float64", "ascii", "utf8", "binary", "bitfield", "bool", "crc", "checksum",
]

TYPE_LABELS = {
    "hex": "HEX", "ascii": "ASCII", "utf8": "UTF-8", "binary": "Binary", "bitfield": "Bit field",
    "bool": "Boolean", "crc": "CRC", "checksum": "Checksum",
}

_TYPE_ALIASES = {
    "u8": "uint8", "u16": "uint16", "u32": "uint32", "u64": "uint64",
    "i8": "int8", "i16": "int16", "i32": "int32", "i64": "int64",
    "byte": "uint8", "word": "uint16", "dword": "uint32",
    "float": "float32", "double": "float64", "single": "float32",
    "utf-8": "utf8", "string": "ascii", "text": "ascii",
    "bytes": "hex", "raw": "hex",
    "bit field": "bitfield", "bit_field": "bitfield", "bits": "bitfield", "flags": "bitfield",
    "boolean": "bool",
    "crc8": "crc", "crc16": "crc", "crc32": "crc", "crc-8": "crc", "crc-16": "crc", "crc-32": "crc",
}
# CRC type aliases that imply an algorithm when none is given.
_CRC_TYPE_DEFAULTS = {"crc8": "CRC-8", "crc-8": "CRC-8", "crc16": "CRC-16", "crc-16": "CRC-16",
                      "crc32": "CRC-32", "crc-32": "CRC-32"}


def normalize_type(name: str) -> str:
    key = str(name or "").strip().lower()
    return _TYPE_ALIASES.get(key, key)


def type_label(name: str) -> str:
    return TYPE_LABELS.get(name, name.upper())


def _parse_values(raw: Any) -> dict[int, str]:
    """Value-name map; keys may be ints, decimal or 0x strings."""
    if not raw:
        return {}
    if not isinstance(raw, dict):
        raise ProtocolError("'values' must be an object mapping numbers to names")
    result: dict[int, str] = {}
    for k, v in raw.items():
        try:
            result[parse_int(k)] = str(v)
        except (ValueError, TypeError) as exc:
            raise ProtocolError(f"Invalid key in 'values': {k!r}") from exc
    return result


def _values_to_json(values: dict[int, str], hex_keys: bool) -> dict[str, str]:
    return {(f"0x{k:02X}" if hex_keys else str(k)): v for k, v in sorted(values.items())}


# ----------------------------------------------------------------- pieces


@dataclass
class BitDefinition:
    name: str
    start: int  # bit 0 = least significant bit
    length: int = 1
    description: str = ""
    values: dict[int, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "BitDefinition":
        if not isinstance(d, dict):
            raise ProtocolError("Bit definitions must be objects")
        try:
            return cls(
                name=str(d.get("name", "")).strip() or f"bit{d.get('start', '?')}",
                start=int(parse_int(d.get("start", d.get("bit", 0)))),
                length=int(parse_int(d.get("length", 1))),
                description=str(d.get("description", "")),
                values=_parse_values(d.get("values")),
            )
        except (TypeError, ValueError) as exc:
            raise ProtocolError(f"Invalid bit definition {d!r}: {exc}") from exc

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"name": self.name, "start": self.start, "length": self.length}
        if self.description:
            d["description"] = self.description
        if self.values:
            d["values"] = _values_to_json(self.values, hex_keys=False)
        return d


@dataclass
class CheckConfig:
    """Configuration of a CRC or checksum field.

    ``range_start`` / ``range_end`` select the covered bytes (inclusive). Each is
    a field name (start of / end of that field) or an integer byte offset
    (negative = from end). Defaults: offset 0 up to the byte before the check.
    """

    algorithm: str = "CRC-16"
    range_start: str | int | None = None
    range_end: str | int | None = None
    params: dict[str, Any] = field(default_factory=dict)  # custom CRC parameters

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None, default_algorithm: str) -> "CheckConfig":
        d = dict(d or {})
        params = {k: d[k] for k in ("width", "poly", "init", "xorout", "refin", "refout") if k in d}
        return cls(
            algorithm=str(d.get("algorithm") or d.get("type") or default_algorithm),
            range_start=_range_ref(d.get("range_start")),
            range_end=_range_ref(d.get("range_end")),
            params=params,
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"algorithm": self.algorithm}
        d.update(self.params)
        if self.range_start is not None:
            d["range_start"] = self.range_start
        if self.range_end is not None:
            d["range_end"] = self.range_end
        return d

    def crc_params(self) -> crc_mod.CRCParams:
        return crc_mod.resolve_crc({"algorithm": self.algorithm, **self.params})


def _range_ref(value: Any) -> str | int | None:
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    try:
        return int(text, 0)
    except ValueError:
        return text


@dataclass
class FieldDefinition:
    name: str
    type: str = "hex"
    offset: int | str = "auto"
    length: int | str | None = None
    length_from: str | None = None
    length_scale: int = 1
    length_adjust: int = 0
    endian: str | None = None  # None = protocol default
    encoding: str | None = None  # text types: override codec (e.g. "latin-1")
    description: str = ""
    expected: bytes | None = None
    values: dict[int, str] = field(default_factory=dict)
    bits: list[BitDefinition] = field(default_factory=list)
    scale: float | None = None
    bias: float | None = None
    unit: str = ""
    null_terminated: bool = False
    check: CheckConfig | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    _KNOWN = {
        "name", "type", "offset", "length", "length_from", "length_scale", "length_adjust", "endian",
        "encoding", "description", "expected", "values", "bits", "scale", "bias", "unit",
        "null_terminated", "crc", "checksum",
    }

    # ------------------------------------------------------------ helpers
    @property
    def is_check(self) -> bool:
        return self.type in CHECK_TYPES

    @property
    def is_integer(self) -> bool:
        return self.type in INTEGER_TYPES or self.type in CHECK_TYPES

    def fixed_size(self) -> int | None:
        """Byte size when it does not depend on packet contents, else None."""
        if self.length_from:
            return None
        if isinstance(self.length, int):
            return self.length
        if self.length == "remaining":
            return None
        if self.type in TYPE_SIZES:
            return TYPE_SIZES[self.type]
        if self.type in ("bitfield", "bool"):
            return 1
        if self.type == "crc" and self.check:
            try:
                return self.check.crc_params().byte_length
            except crc_mod.CRCError:
                return None
        if self.type == "checksum" and self.check:
            try:
                return checksum_mod.get_checksum(self.check.algorithm).byte_length
            except ValueError:
                return None
        return None

    def offset_text(self) -> str:
        if self.offset == "auto":
            return "Auto"
        if self.offset == "last":
            return "Last"
        return str(self.offset)

    def length_text(self) -> str:
        if self.length_from:
            text = f"= {self.length_from}"
            if self.length_scale != 1:
                text += f" × {self.length_scale}"
            if self.length_adjust:
                text += f" {'+' if self.length_adjust > 0 else '-'} {abs(self.length_adjust)}"
            return text
        if self.length == "remaining":
            return "Remaining"
        size = self.fixed_size()
        return str(size) if size is not None else "?"

    # --------------------------------------------------------- serialise
    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "FieldDefinition":
        if not isinstance(d, dict):
            raise ProtocolError("Each field must be a JSON object")
        name = str(d.get("name", "")).strip()
        if not name:
            raise ProtocolError("Field without a name")
        raw_type = str(d.get("type", "hex"))
        ftype = normalize_type(raw_type)
        where = f"field '{name}'"

        offset: int | str = d.get("offset", "auto")
        if offset is None or (isinstance(offset, str) and offset.strip().lower() in ("", "auto", "dynamic", "next")):
            offset = "auto"
        elif isinstance(offset, str) and offset.strip().lower() in ("last", "end"):
            offset = "last"
        else:
            try:
                offset = int(parse_int(offset))
            except (TypeError, ValueError) as exc:
                raise ProtocolError(f"Invalid offset {offset!r} in {where}") from exc

        length: int | str | None = d.get("length")
        if isinstance(length, str):
            low = length.strip().lower()
            if low in ("remaining", "rest"):
                length = "remaining"
            elif low in ("", "dynamic", "auto"):
                length = None
            else:
                try:
                    length = int(parse_int(length))
                except ValueError as exc:
                    raise ProtocolError(f"Invalid length {length!r} in {where}") from exc
        elif length is not None and not isinstance(length, int):
            raise ProtocolError(f"Invalid length {length!r} in {where}")

        endian = d.get("endian")
        if endian is not None:
            endian = _norm_endian(endian, where)

        check = None
        if ftype == "crc":
            default_alg = _CRC_TYPE_DEFAULTS.get(raw_type.strip().lower(), "CRC-16")
            check = CheckConfig.from_dict(d.get("crc"), default_alg)
        elif ftype == "checksum":
            check = CheckConfig.from_dict(d.get("checksum"), "sum8")

        try:
            expected = parse_hex_bytes(d.get("expected"))
        except (HexParseError, ValueError) as exc:
            raise ProtocolError(f"Invalid expected value in {where}: {exc}") from exc

        bits_raw = d.get("bits") or []
        if not isinstance(bits_raw, list):
            raise ProtocolError(f"'bits' must be a list in {where}")

        try:
            return cls(
                name=name,
                type=ftype,
                offset=offset,
                length=length,
                length_from=(str(d["length_from"]).strip() or None) if d.get("length_from") else None,
                length_scale=int(parse_int(d.get("length_scale", 1))),
                length_adjust=int(parse_int(d.get("length_adjust", 0))),
                endian=endian,
                encoding=d.get("encoding") or None,
                description=str(d.get("description", "")),
                expected=expected,
                values=_parse_values(d.get("values")),
                bits=[BitDefinition.from_dict(b) for b in bits_raw],
                scale=float(d["scale"]) if d.get("scale") not in (None, "") else None,
                bias=float(d["bias"]) if d.get("bias") not in (None, "") else None,
                unit=str(d.get("unit", "")),
                null_terminated=bool(d.get("null_terminated", False)),
                check=check,
                extra={k: v for k, v in d.items() if k not in cls._KNOWN},
            )
        except (TypeError, ValueError) as exc:
            if isinstance(exc, ProtocolError):
                raise
            raise ProtocolError(f"Invalid value in {where}: {exc}") from exc

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"name": self.name, "type": self.type}
        if self.offset != "auto":
            d["offset"] = self.offset
        if self.length_from:
            d["length_from"] = self.length_from
            if self.length_scale != 1:
                d["length_scale"] = self.length_scale
            if self.length_adjust:
                d["length_adjust"] = self.length_adjust
        elif self.length is not None:
            d["length"] = self.length
        if self.endian:
            d["endian"] = self.endian
        if self.encoding:
            d["encoding"] = self.encoding
        if self.expected is not None:
            d["expected"] = format_hex(self.expected, sep="")
        if self.values:
            d["values"] = _values_to_json(self.values, hex_keys=True)
        if self.bits:
            d["bits"] = [b.to_dict() for b in self.bits]
        if self.scale is not None:
            d["scale"] = self.scale
        if self.bias is not None:
            d["bias"] = self.bias
        if self.unit:
            d["unit"] = self.unit
        if self.null_terminated:
            d["null_terminated"] = True
        if self.check is not None:
            d["crc" if self.type == "crc" else "checksum"] = self.check.to_dict()
        if self.description:
            d["description"] = self.description
        d.update(self.extra)
        return d


def _norm_endian(value: Any, where: str) -> str:
    text = str(value).strip().lower()
    if text in ("little", "le", "l", "<"):
        return "little"
    if text in ("big", "be", "b", ">", "network"):
        return "big"
    raise ProtocolError(f"Invalid endianness {value!r} in {where} (use 'little' or 'big')")


@dataclass
class FrameSpec:
    start_bytes: bytes | None = None
    end_bytes: bytes | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "FrameSpec":
        d = d or {}
        try:
            return cls(parse_hex_bytes(d.get("start_byte")), parse_hex_bytes(d.get("end_byte")))
        except (HexParseError, ValueError) as exc:
            raise ProtocolError(f"Invalid frame start/end byte: {exc}") from exc

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.start_bytes:
            d["start_byte"] = format_hex(self.start_bytes, sep="")
        if self.end_bytes:
            d["end_byte"] = format_hex(self.end_bytes, sep="")
        return d


@dataclass
class ExamplePacket:
    name: str
    hex: str
    direction: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = {"name": self.name, "hex": self.hex}
        if self.direction:
            d["direction"] = self.direction
        return d


@dataclass
class ProtocolIssue:
    severity: str  # "error" | "warning"
    message: str

    def __str__(self) -> str:
        return f"{self.severity.upper()}: {self.message}"


@dataclass
class ProtocolDefinition:
    name: str
    description: str = ""
    version: str = "1.0"
    default_endian: str = "big"
    frame: FrameSpec = field(default_factory=FrameSpec)
    fields: list[FieldDefinition] = field(default_factory=list)
    examples: list[ExamplePacket] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
    source_path: str | None = None  # not serialised

    _KNOWN = {"schema_version", "name", "description", "version", "default_endian", "frame", "fields", "examples"}

    def field_by_name(self, name: str) -> FieldDefinition | None:
        for f in self.fields:
            if f.name == name:
                return f
        return None

    def clone(self) -> "ProtocolDefinition":
        return copy.deepcopy(self)

    # --------------------------------------------------------- serialise
    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ProtocolDefinition":
        if not isinstance(d, dict):
            raise ProtocolError("Invalid protocol definition: top level must be a JSON object")
        name = str(d.get("name", "")).strip()
        if not name:
            raise ProtocolError("Invalid protocol definition: missing 'name'")
        fields_raw = d.get("fields")
        if not isinstance(fields_raw, list):
            raise ProtocolError(f"Invalid protocol definition '{name}': 'fields' must be a list")
        examples = []
        for ex in d.get("examples") or []:
            if isinstance(ex, dict) and ex.get("hex"):
                examples.append(ExamplePacket(str(ex.get("name", "Example")), str(ex["hex"]),
                                              str(ex.get("direction", ""))))
        try:
            fields = [FieldDefinition.from_dict(f) for f in fields_raw]
        except ProtocolError as exc:
            raise ProtocolError(f"Invalid protocol definition '{name}': {exc}") from exc
        return cls(
            name=name,
            description=str(d.get("description", "")),
            version=str(d.get("version", "1.0")),
            default_endian=_norm_endian(d.get("default_endian", "big"), "protocol"),
            frame=FrameSpec.from_dict(d.get("frame")),
            fields=fields,
            examples=examples,
            extra={k: v for k, v in d.items() if k not in cls._KNOWN},
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "default_endian": self.default_endian,
        }
        frame = self.frame.to_dict()
        if frame:
            d["frame"] = frame
        d["fields"] = [f.to_dict() for f in self.fields]
        if self.examples:
            d["examples"] = [e.to_dict() for e in self.examples]
        d.update(self.extra)
        return d

    # ---------------------------------------------------------- validate
    def validate(self) -> list[ProtocolIssue]:
        """Static checks of the definition. Errors make decoding impossible."""
        issues: list[ProtocolIssue] = []

        def err(msg: str) -> None:
            issues.append(ProtocolIssue("error", msg))

        def warn(msg: str) -> None:
            issues.append(ProtocolIssue("warning", msg))

        if not self.name.strip():
            err("Protocol name is empty")
        if not self.fields:
            err("Protocol has no fields")

        seen: dict[str, int] = {}
        remaining_seen = False
        last_seen = False
        for idx, f in enumerate(self.fields):
            where = f"Field '{f.name}'"
            if f.name in seen:
                err(f"Duplicate field name '{f.name}'")
            seen[f.name] = idx

            if f.type not in FIELD_TYPES:
                err(f"{where}: unsupported field type '{f.type}'")
                continue

            if last_seen:
                warn(f"{where} follows a field anchored at the end of the packet ('last')")
            if f.offset == "last":
                last_seen = True

            # Length rules
            if f.length_from:
                ref_idx = seen.get(f.length_from)
                if ref_idx is None or ref_idx == idx:
                    err(f"{where}: length_from refers to '{f.length_from}', which is not an earlier field")
                else:
                    ref = self.fields[ref_idx]
                    if ref.type not in TYPE_SIZES or ref.type.startswith("float"):
                        if ref.type not in ("bitfield",):
                            err(f"{where}: length_from field '{ref.name}' must be an integer type")
                if f.length_scale < 1:
                    err(f"{where}: length_scale must be >= 1")
            elif f.length == "remaining":
                if remaining_seen:
                    err(f"{where}: only one field may use length 'remaining'")
                remaining_seen = True
                if f.offset == "last":
                    err(f"{where}: a 'remaining' field cannot use offset 'last'")
                for later in self.fields[idx + 1:]:
                    if later.fixed_size() is None:
                        err(f"Field '{later.name}' follows a 'remaining' field and must have a fixed length")
                    if later.offset not in ("auto", "last"):
                        err(f"Field '{later.name}' follows a 'remaining' field and must use offset 'auto' or 'last'")
            elif isinstance(f.length, int):
                if f.length < 0:
                    err(f"{where}: negative length")
                elif f.length == 0:
                    warn(f"{where}: zero length")
                if f.type in TYPE_SIZES and f.length != TYPE_SIZES[f.type]:
                    err(f"{where}: type {f.type} requires length {TYPE_SIZES[f.type]} (got {f.length})")
                if f.type in ("bitfield", "bool") and not 1 <= f.length <= 8:
                    err(f"{where}: {f.type} length must be 1..8 bytes")
            elif f.type in VARIABLE_TYPES:
                err(f"{where}: type {f.type} needs a length, 'length_from' or 'remaining'")

            if f.offset == "last" and f.fixed_size() is None and not f.length_from:
                err(f"{where}: offset 'last' requires a known length")

            # Bit definitions
            size = f.fixed_size()
            if f.bits:
                if not (f.type in INTEGER_TYPES or f.type == "hex"):
                    err(f"{where}: bit definitions are only allowed on integer/bitfield fields")
                elif size is not None:
                    width = size * 8
                    for b in f.bits:
                        if b.length < 1 or b.start < 0 or b.start + b.length > width:
                            err(f"{where}: bit '{b.name}' (start {b.start}, length {b.length}) exceeds {width} bits")

            # Checks
            if f.type == "crc":
                try:
                    params = f.check.crc_params() if f.check else crc_mod.get_preset("CRC-16")
                    if isinstance(f.length, int) and f.length != params.byte_length:
                        err(f"{where}: {params.name} is {params.byte_length} byte(s) but field length is {f.length}")
                except crc_mod.CRCError as exc:
                    err(f"{where}: {exc}")
            elif f.type == "checksum":
                try:
                    alg = checksum_mod.get_checksum(f.check.algorithm if f.check else "sum8")
                    if isinstance(f.length, int) and f.length != alg.byte_length:
                        err(f"{where}: checksum {alg.name} is {alg.byte_length} byte(s) but field length is {f.length}")
                except ValueError as exc:
                    err(f"{where}: {exc}")
            if f.check:
                for ref in (f.check.range_start, f.check.range_end):
                    if isinstance(ref, str) and ref not in {x.name for x in self.fields}:
                        err(f"{where}: check range refers to unknown field '{ref}'")

            if f.type in TEXT_TYPES and f.encoding:
                try:
                    "".encode(f.encoding)
                except LookupError:
                    err(f"{where}: unknown text encoding '{f.encoding}'")
        return issues

    def errors(self) -> list[str]:
        return [i.message for i in self.validate() if i.severity == "error"]
