"""HEX text parsing and formatting helpers.

All user-entered packet text goes through :func:`parse_hex`, which accepts the
common notations engineers copy out of terminals, logic analysers and source
code, and rejects anything ambiguous with a descriptive error message.
"""
from __future__ import annotations

import re

__all__ = [
    "HexParseError",
    "parse_hex",
    "try_parse_hex",
    "format_hex",
    "format_hex_compact",
    "parse_int",
    "parse_hex_bytes",
]


class HexParseError(ValueError):
    """Raised when text cannot be converted into packet bytes."""


_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
# Token separators: whitespace, comma, semicolon, colon, dash, pipe.
_TOKEN_RE = re.compile(r"[^\s,;:\-|]+")
_PREFIX_RE = re.compile(r"0x|\\x", re.IGNORECASE)
_COMMENT_RE = re.compile(r"(#|//).*$")


def _strip_comment(line: str) -> str:
    return _COMMENT_RE.sub("", line)


def parse_hex(text: str) -> bytes:
    """Convert HEX text into bytes.

    Accepted notations (freely mixed, multi-line allowed)::

        02 01 08 00          0x02 0x01 0x08     02,01,08,00
        02:01:08             020108             \\x02\\x01\\x08

    ``#`` and ``//`` start a comment that runs to the end of the line.

    Raises:
        HexParseError: on empty input, invalid characters, an odd number of
            HEX digits, or a prefixed value that does not fit in one byte.
    """
    if text is None or not str(text).strip():
        raise HexParseError("Empty packet: no HEX data entered")

    out = bytearray()
    for line_no, raw_line in enumerate(str(text).splitlines(), start=1):
        line = _strip_comment(raw_line)
        for match in _TOKEN_RE.finditer(line):
            token = match.group(0)
            column = match.start() + 1
            out.extend(_parse_token(token, line_no, column))

    if not out:
        raise HexParseError("Empty packet: no HEX bytes found in input")
    return bytes(out)


def _parse_token(token: str, line_no: int, column: int) -> bytes:
    where = f"line {line_no}, column {column}"
    if _PREFIX_RE.match(token):
        # One or more prefixed values, e.g. "0x02" or "\x02\x01".
        parts = _PREFIX_RE.split(token)[1:]
        result = bytearray()
        for part in parts:
            if not part:
                raise HexParseError(f"Invalid HEX data: prefix without digits in '{token}' ({where})")
            _check_digits(part, token, where)
            if len(part) == 1:
                part = "0" + part
            elif len(part) % 2:
                raise HexParseError(
                    f"Invalid byte value '{token}' ({where}): odd number of HEX digits "
                    f"- a byte must be written as 1 or 2 digits"
                )
            result.extend(bytes.fromhex(part))
        return bytes(result)

    _check_digits(token, token, where)
    if len(token) % 2:
        raise HexParseError(
            f"Odd number of HEX characters in '{token}' ({where}). "
            f"Each byte needs two HEX digits, e.g. '0{token}' or '{token}0'"
        )
    return bytes.fromhex(token)


def _check_digits(part: str, token: str, where: str) -> None:
    for i, ch in enumerate(part):
        if ch not in _HEX_DIGITS:
            raise HexParseError(f"Invalid HEX data: invalid character '{ch}' in '{token}' ({where})")


def try_parse_hex(text: str) -> tuple[bytes | None, str | None]:
    """Like :func:`parse_hex` but returns ``(data, error_message)``."""
    try:
        return parse_hex(text), None
    except HexParseError as exc:
        return None, str(exc)


def format_hex(data: bytes | bytearray, sep: str = " ", upper: bool = True) -> str:
    """Format bytes as ``02 01 08`` (or with another separator)."""
    text = sep.join(f"{b:02X}" if upper else f"{b:02x}" for b in data)
    return text


def format_hex_compact(data: bytes, max_bytes: int = 8) -> str:
    """Format bytes, eliding the middle of long sequences."""
    if len(data) <= max_bytes:
        return format_hex(data)
    head = format_hex(data[: max_bytes - 2])
    tail = format_hex(data[-2:])
    return f"{head} … {tail}"


def parse_int(value: int | str | None, default: int | None = None) -> int | None:
    """Parse an integer given as int, decimal string, ``0x`` HEX or ``0b`` binary."""
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    text = str(value).strip().replace("_", "")
    try:
        return int(text, 0)
    except ValueError:
        # Leading zeros ("010") are rejected by base 0; fall back to decimal.
        try:
            return int(text, 10)
        except ValueError as exc:
            raise ValueError(f"Not a valid integer: '{value}'") from exc


def parse_hex_bytes(value: str | int | None) -> bytes | None:
    """Parse a short configuration value such as ``"02"``, ``"0xAA55"`` or ``2``."""
    if value is None or value == "":
        return None
    if isinstance(value, int):
        if not 0 <= value <= 0xFF:
            raise ValueError(f"Byte value out of range: {value}")
        return bytes([value])
    return parse_hex(str(value))
