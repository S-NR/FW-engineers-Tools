"""File helpers: JSON persistence and HEX packet extraction from capture files."""
from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from utils.hex_utils import HexParseError, parse_hex

__all__ = [
    "ExtractedPacket",
    "PACKET_FILE_FILTER",
    "load_json",
    "save_json",
    "read_text_file",
    "extract_packets",
    "load_packets_from_file",
]

PACKET_FILE_FILTER = (
    "Packet files (*.txt *.hex *.log *.csv *.bin);;"
    "Text (*.txt);;HEX (*.hex);;Logs (*.log);;CSV (*.csv);;Raw binary (*.bin);;All files (*)"
)

_BYTE_TOKEN = re.compile(r"^(?:0x|\\x)?[0-9A-Fa-f]{2}$", re.IGNORECASE)
_LONG_HEX = re.compile(r"^(?:[0-9A-Fa-f]{2}){2,}$")
_DIRECTION = re.compile(r"\b(TX|RX|SEND|SENT|RECV|RECEIVED|OUT|IN)\b", re.IGNORECASE)
_INTEL_HEX = re.compile(r"^:([0-9A-Fa-f]{2})+$")
_CELL_SPLIT = re.compile(r"[,\t;]")
_TOKEN_SPLIT = re.compile(r"[\s,;\t]+")
_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:[.,]\d+)?)?"  # ISO date [time]
    r"|\b\d{1,2}:\d{2}:\d{2}(?:[.,]\d+)?\b"                          # 12:32:10.123
    r"|\[\s*\d+(?:\.\d+)?\s*\]"                                      # [123.456]
)


@dataclass
class ExtractedPacket:
    data: bytes
    line_no: int
    direction: str = ""
    source: str = ""


# --------------------------------------------------------------------- JSON


def load_json(path: str | os.PathLike, default: Any = None) -> Any:
    """Load JSON, returning ``default`` when the file is missing.

    A malformed file raises ``ValueError`` with the path in the message.
    """
    p = Path(path)
    if not p.exists():
        return default
    try:
        with p.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {p.name}: line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc


def save_json(path: str | os.PathLike, data: Any) -> None:
    """Write JSON atomically (temp file + rename) so a crash never truncates it."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=p.name, suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, indent=4, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ------------------------------------------------------------ packet files


def read_text_file(path: str | os.PathLike) -> str:
    raw = Path(path).read_bytes()
    encoding = "utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig"
    try:
        return raw.decode(encoding)
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _detect_direction(line: str) -> str:
    m = _DIRECTION.search(line)
    if not m:
        return ""
    word = m.group(1).upper()
    return "TX" if word in ("TX", "SEND", "SENT", "OUT") else "RX"


def _longest_byte_run(tokens: list[str]) -> bytes:
    """Longest run of consecutive tokens that are each a HEX byte (or a long HEX string)."""
    best: bytes = b""
    current = bytearray()
    for tok in tokens:
        if _BYTE_TOKEN.match(tok) or _LONG_HEX.match(tok):
            try:
                current.extend(parse_hex(tok))
                continue
            except HexParseError:
                pass
        if len(current) > len(best):
            best = bytes(current)
        current = bytearray()
    if len(current) > len(best):
        best = bytes(current)
    return best


def _extract_from_line(line: str) -> bytes:
    stripped = line.strip()
    if not stripped:
        return b""

    # Intel HEX record ":LLAAAATT<data>CC" - return the data bytes of type 00 records.
    if _INTEL_HEX.match(stripped):
        rec = bytes.fromhex(stripped[1:])
        if len(rec) >= 5 and rec[3] == 0x00 and len(rec) == rec[0] + 5:
            return rec[4 : 4 + rec[0]]
        return b""

    # Timestamps/dates would otherwise parse as HEX ("12:32:10" -> 12 32 10).
    stripped = _TIMESTAMP.sub(" ", stripped).strip()
    if not stripped:
        return b""

    # Whole line is valid HEX in any accepted notation (e.g. "0x02,0x01" or "02 01").
    try:
        return parse_hex(stripped)
    except HexParseError:
        pass

    # CSV-like: prefer the single cell containing the most bytes, so columns
    # such as a timestamp or a length are not glued onto the packet.
    cells = _CELL_SPLIT.split(stripped)
    best_cell = b""
    if len(cells) > 1:
        for cell in cells:
            run = _longest_byte_run(cell.split())
            if len(run) > len(best_cell):
                best_cell = run
    if len(best_cell) >= 2:
        return best_cell

    return _longest_byte_run(_TOKEN_SPLIT.split(stripped))


def extract_packets(text: str, min_bytes: int = 1) -> list[ExtractedPacket]:
    """Extract one packet per line from free-form text (logs, CSV, dumps)."""
    packets: list[ExtractedPacket] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        data = _extract_from_line(line)
        if len(data) >= min_bytes:
            packets.append(ExtractedPacket(data, line_no, _detect_direction(line), line.strip()))
    return packets


def load_packets_from_file(path: str | os.PathLike) -> list[ExtractedPacket]:
    """Load packets from ``.txt/.hex/.log/.csv`` (text) or ``.bin`` (one raw packet)."""
    p = Path(path)
    if p.suffix.lower() == ".bin":
        data = p.read_bytes()
        return [ExtractedPacket(data, 1, "", p.name)] if data else []
    return extract_packets(read_text_file(p))
