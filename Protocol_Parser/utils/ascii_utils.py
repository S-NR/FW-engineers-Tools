"""ASCII rendering helpers for hex dumps and text fields."""
from __future__ import annotations

from dataclasses import dataclass

from utils.hex_utils import format_hex

__all__ = ["is_printable", "to_ascii", "escape_bytes", "HexDumpRow", "hexdump_rows", "hexdump_text"]


def is_printable(byte: int) -> bool:
    """True for printable 7-bit ASCII (space .. tilde)."""
    return 0x20 <= byte < 0x7F


def to_ascii(data: bytes, placeholder: str = ".") -> str:
    """Render bytes as ASCII, replacing non-printable bytes with ``placeholder``."""
    return "".join(chr(b) if is_printable(b) else placeholder for b in data)


def escape_bytes(data: bytes) -> str:
    """Render bytes as text, escaping non-printables as ``\\xNN`` (plus \\r \\n \\t)."""
    out = []
    for b in data:
        if b == 0x0A:
            out.append("\\n")
        elif b == 0x0D:
            out.append("\\r")
        elif b == 0x09:
            out.append("\\t")
        elif b == 0x5C:
            out.append("\\\\")
        elif is_printable(b):
            out.append(chr(b))
        else:
            out.append(f"\\x{b:02X}")
    return "".join(out)


@dataclass(frozen=True)
class HexDumpRow:
    offset: int
    data: bytes

    @property
    def hex_text(self) -> str:
        return format_hex(self.data)

    @property
    def ascii_text(self) -> str:
        return to_ascii(self.data)


def hexdump_rows(data: bytes, width: int = 16) -> list[HexDumpRow]:
    width = max(1, width)
    return [HexDumpRow(i, data[i : i + width]) for i in range(0, len(data), width)]


def hexdump_text(data: bytes, width: int = 16) -> str:
    """Classic ``offset  hex  |ascii|`` dump as plain text."""
    lines = []
    for row in hexdump_rows(data, width):
        lines.append(f"{row.offset:04X}  {row.hex_text.ljust(width * 3 - 1)}  |{row.ascii_text}|")
    return "\n".join(lines)
