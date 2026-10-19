"""Packet encoder: the inverse of the decoder.

Builds a packet from field values, filling in automatically:
  * frame start/end bytes and fields with an ``expected`` value,
  * length fields referenced by ``length_from``,
  * CRC / checksum fields.

Supports sequential layouts (``auto``/``last`` offsets, or absolute offsets
that match the running position), which covers typical framed protocols.
Foundation for packet replay and test-sequence generation.
"""
from __future__ import annotations

import struct
from typing import Any

from core.decoder import decode_packet
from core.protocol import TYPE_SIZES, FieldDefinition, ProtocolDefinition
from utils.hex_utils import parse_hex

__all__ = ["EncodeError", "encode_field_value", "user_fields", "encode_packet"]


class EncodeError(ValueError):
    """Values cannot be encoded with the given protocol."""


def _order(f: FieldDefinition, protocol: ProtocolDefinition) -> str:
    return "little" if (f.endian or protocol.default_endian) == "little" else "big"


def user_fields(protocol: ProtocolDefinition) -> list[FieldDefinition]:
    """Fields whose value must be supplied by the user (not auto-computed)."""
    auto = _auto_field_names(protocol)
    return [f for f in protocol.fields if f.name not in auto]


def _auto_field_names(protocol: ProtocolDefinition) -> set[str]:
    names = {f.length_from for f in protocol.fields if f.length_from}
    for i, f in enumerate(protocol.fields):
        if f.is_check or f.expected is not None:
            names.add(f.name)
        if i == 0 and protocol.frame.start_bytes and f.fixed_size() == len(protocol.frame.start_bytes):
            names.add(f.name)
        if i == len(protocol.fields) - 1 and protocol.frame.end_bytes and \
                f.fixed_size() == len(protocol.frame.end_bytes):
            names.add(f.name)
    return names


def encode_field_value(f: FieldDefinition, value: Any, order: str) -> bytes:
    """Encode one user value. Strings are accepted for every type (as typed in a GUI)."""
    t = f.type
    try:
        if t in ("hex", "binary"):
            if isinstance(value, (bytes, bytearray)):
                return bytes(value)
            text = str(value)
            if t == "binary":
                bits = "".join(text.split())
                if not bits or set(bits) - {"0", "1"} or len(bits) % 8:
                    raise EncodeError(f"'{f.name}': binary value must be a multiple of 8 bits of 0/1")
                return int(bits, 2).to_bytes(len(bits) // 8, "big")
            return parse_hex(text) if text.strip() else b""
        if t in ("ascii", "utf8"):
            codec = f.encoding or ("ascii" if t == "ascii" else "utf-8")
            data = str(value).encode(codec)
            return data + (b"\x00" if f.null_terminated else b"")
        if t in ("float32", "float64"):
            x = float(value)
            if f.scale or f.bias:
                x = (x - (f.bias or 0)) / (f.scale or 1)
            return struct.pack(("<" if order == "little" else ">") + ("f" if t == "float32" else "d"), x)
        if t in TYPE_SIZES or t in ("bitfield", "bool", "crc", "checksum"):
            size = f.fixed_size() or 1
            if isinstance(value, str):
                text = value.strip()
                if t == "bool" and text.lower() in ("true", "false", "yes", "no", "on", "off"):
                    number: float = 1 if text.lower() in ("true", "yes", "on") else 0
                else:
                    # Accept a name from the value map ("Read Coils"), 0x/0b literals or decimals.
                    reverse = {v.lower(): k for k, v in f.values.items()}
                    if text.lower() in reverse:
                        number = reverse[text.lower()]
                    elif text.lower().startswith(("0x", "0b", "-0x")):
                        number = int(text, 0)
                    else:
                        number = float(text)
            else:
                number = float(value)
            if f.scale or f.bias:
                number = (number - (f.bias or 0)) / (f.scale or 1)
            ivalue = int(round(number))
            signed = t.startswith("int")
            return ivalue.to_bytes(size, order, signed=signed)
    except EncodeError:
        raise
    except (ValueError, OverflowError, UnicodeEncodeError, struct.error) as exc:
        raise EncodeError(f"'{f.name}': cannot encode {value!r} as {t}: {exc}") from exc
    raise EncodeError(f"'{f.name}': unsupported field type '{t}'")


def encode_packet(protocol: ProtocolDefinition, values: dict[str, Any]) -> bytes:
    errors = protocol.errors()
    if errors:
        raise EncodeError("Invalid protocol definition: " + "; ".join(errors))

    fields = protocol.fields
    chunks: dict[str, bytes] = {}
    last_index = len(fields) - 1

    # Pass 1: plain values, fixed bytes and placeholders.
    for i, f in enumerate(fields):
        order = _order(f, protocol)
        size = f.fixed_size()
        if f.name in values and not f.is_check:
            chunks[f.name] = encode_field_value(f, values[f.name], order)
        elif f.expected is not None:
            chunks[f.name] = f.expected
        elif i == 0 and protocol.frame.start_bytes and size == len(protocol.frame.start_bytes):
            chunks[f.name] = protocol.frame.start_bytes
        elif i == last_index and protocol.frame.end_bytes and size == len(protocol.frame.end_bytes):
            chunks[f.name] = protocol.frame.end_bytes
        elif f.is_check or any(o.length_from == f.name for o in fields):
            chunks[f.name] = bytes(size or 0)  # filled later
        else:
            raise EncodeError(f"No value given for field '{f.name}'")
        if size is not None and len(chunks[f.name]) != size:
            raise EncodeError(f"'{f.name}' must be {size} byte(s), got {len(chunks[f.name])}")

    # Pass 2: length fields.
    for f in fields:
        if f.length_from and f.length_from not in values:
            ref = protocol.field_by_name(f.length_from)
            raw_len = len(chunks[f.name]) - f.length_adjust
            if raw_len < 0 or raw_len % f.length_scale:
                raise EncodeError(f"Length of '{f.name}' cannot be expressed in '{ref.name}'")
            try:
                chunks[ref.name] = (raw_len // f.length_scale).to_bytes(
                    ref.fixed_size(), _order(ref, protocol), signed=ref.type.startswith("int"))
            except OverflowError as exc:
                raise EncodeError(f"'{f.name}' is too long for length field '{ref.name}'") from exc

    # Assemble (sequential layout).
    packet = bytearray()
    for f in fields:
        if isinstance(f.offset, int) and f.offset >= 0 and f.offset != len(packet):
            raise EncodeError(f"'{f.name}': absolute offset {f.offset} does not follow the previous field "
                              f"(position {len(packet)}); encoder supports sequential layouts only")
        packet.extend(chunks[f.name])

    # Pass 3: checks, in field order (a later check may cover an earlier one).
    for f in fields:
        if not f.is_check:
            continue
        result = decode_packet(protocol, bytes(packet))
        check = next((c for df in result.fields if df.name == f.name and (c := df.check)), None)
        if check is None or check.calculated is None:
            raise EncodeError(f"Cannot compute '{f.name}': {check.error if check else 'field not decodable'}")
        offset = next(df.offset for df in result.fields if df.name == f.name and df.defined)
        packet[offset: offset + len(check.calculated)] = check.calculated

    return bytes(packet)
