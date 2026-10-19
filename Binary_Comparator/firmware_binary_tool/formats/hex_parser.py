"""Intel HEX reader / writer.

Supports record types 00 (data), 01 (EOF), 02 (extended segment address),
03 (start segment address), 04 (extended linear address) and
05 (start linear address). Every record's length and checksum is validated;
malformed input raises :class:`FirmwareFormatError` with the line number.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.image import Segment
from ..errors import FirmwareFormatError

_HEX_DIGITS = set("0123456789abcdefABCDEF")


@dataclass
class HexParseResult:
    segments: list[Segment]
    warnings: list[str] = field(default_factory=list)
    info: dict[str, str] = field(default_factory=dict)


def parse_intel_hex(path: str) -> HexParseResult:
    warnings: list[str] = []
    info: dict[str, str] = {}
    chunks: list[list] = []  # [start_address, bytearray]
    upper = 0  # base from record 02 / 04
    eof_line = None
    records = 0
    try:
        with open(path, "r", encoding="ascii", newline=None) as f:
            for lineno, raw in enumerate(f, 1):
                line = raw.strip()
                if not line:
                    continue
                if eof_line is not None:
                    warnings.append(f"Data after EOF record (line {lineno}) was ignored.")
                    break
                rec = _decode_line(line, lineno)
                records += 1
                count, offset, rtype, payload = rec
                if rtype == 0x00:
                    addr = upper + offset
                    if chunks and chunks[-1][0] + len(chunks[-1][1]) == addr:
                        chunks[-1][1] += payload
                    else:
                        chunks.append([addr, bytearray(payload)])
                elif rtype == 0x01:
                    eof_line = lineno
                elif rtype == 0x02:
                    _expect_len(count, 2, lineno, rtype)
                    upper = int.from_bytes(payload, "big") << 4
                elif rtype == 0x04:
                    _expect_len(count, 2, lineno, rtype)
                    upper = int.from_bytes(payload, "big") << 16
                elif rtype == 0x03:
                    _expect_len(count, 4, lineno, rtype)
                    info["Start segment address"] = f"{payload[:2].hex().upper()}:{payload[2:].hex().upper()}"
                elif rtype == 0x05:
                    _expect_len(count, 4, lineno, rtype)
                    info["Start linear address"] = f"0x{int.from_bytes(payload, 'big'):08X}"
                else:
                    raise FirmwareFormatError(f"Line {lineno}: unknown record type 0x{rtype:02X}")
    except UnicodeDecodeError:
        raise FirmwareFormatError("File contains non-ASCII bytes - it is not an Intel HEX text file") from None

    if records == 0:
        warnings.append("File contains no Intel HEX records (empty image).")
    elif eof_line is None:
        warnings.append("No EOF record (type 01) found - file may be truncated.")

    chunks.sort(key=lambda c: c[0])
    merged: list[list] = []
    for start, data in chunks:
        if merged:
            pstart, pdata = merged[-1]
            pend = pstart + len(pdata)
            if start < pend:
                raise FirmwareFormatError(
                    f"Overlapping data records: address 0x{start:08X} is defined more than once "
                    f"(previous data covers 0x{pstart:08X}-0x{pend - 1:08X})"
                )
            if start == pend:
                pdata += data
                continue
        merged.append([start, data])

    segments = [Segment(s, bytes(d), None, f"hex segment {i}") for i, (s, d) in enumerate(merged)]
    info["Records"] = str(records)
    info["Data segments"] = str(len(segments))
    return HexParseResult(segments, warnings, info)


def _decode_line(line: str, lineno: int) -> tuple[int, int, int, bytes]:
    if line[0] != ":":
        raise FirmwareFormatError(f"Line {lineno}: record does not start with ':'")
    body = line[1:]
    if len(body) < 10 or len(body) % 2:
        raise FirmwareFormatError(f"Line {lineno}: record has invalid length")
    if not set(body) <= _HEX_DIGITS:
        raise FirmwareFormatError(f"Line {lineno}: record contains non-hex characters")
    raw = bytes.fromhex(body)
    count = raw[0]
    if len(raw) != count + 5:
        raise FirmwareFormatError(
            f"Line {lineno}: byte count field says {count} data bytes but record holds {len(raw) - 5}"
        )
    if sum(raw) & 0xFF:
        expected = (-sum(raw[:-1])) & 0xFF
        raise FirmwareFormatError(
            f"Line {lineno}: checksum error (record has 0x{raw[-1]:02X}, expected 0x{expected:02X})"
        )
    offset = (raw[1] << 8) | raw[2]
    return count, offset, raw[3], raw[4:-1]


def _expect_len(count: int, expected: int, lineno: int, rtype: int) -> None:
    if count != expected:
        raise FirmwareFormatError(
            f"Line {lineno}: record type 0x{rtype:02X} must carry {expected} bytes, has {count}"
        )


def _record(rtype: int, offset: int, payload: bytes) -> str:
    raw = bytes([len(payload), (offset >> 8) & 0xFF, offset & 0xFF, rtype]) + payload
    return ":" + (raw + bytes([(-sum(raw)) & 0xFF])).hex().upper()


def write_intel_hex(segments: list[tuple[int, bytes]], path: str, record_size: int = 16,
                    start_linear: int | None = None) -> None:
    """Write (address, data) segments as Intel HEX (used for examples/tests)."""
    lines: list[str] = []
    upper = None
    for start, data in sorted(segments):
        pos = 0
        while pos < len(data):
            addr = start + pos
            if addr >> 16 != upper:
                upper = addr >> 16
                lines.append(_record(0x04, 0, upper.to_bytes(2, "big")))
            n = min(record_size, len(data) - pos, 0x10000 - (addr & 0xFFFF))
            lines.append(_record(0x00, addr & 0xFFFF, bytes(data[pos:pos + n])))
            pos += n
    if start_linear is not None:
        lines.append(_record(0x05, 0, start_linear.to_bytes(4, "big")))
    lines.append(":00000001FF")
    with open(path, "w", encoding="ascii", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
