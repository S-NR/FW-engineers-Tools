"""Number / address formatting helpers shared by the GUI, CLI and reports."""

from __future__ import annotations


def addr_width(max_addr: int | None) -> int:
    """Hex digits needed to show addresses up to *max_addr* (min 8)."""
    if max_addr is None or max_addr <= 0xFFFFFFFF:
        return 8
    return 16


def fmt_addr(addr: int | None, width: int = 8) -> str:
    if addr is None:
        return "-"
    return f"0x{addr:0{width}X}"


def fmt_offset(offset: int | None) -> str:
    """File offsets are shown with a '+' prefix so they never look like MCU addresses."""
    if offset is None:
        return "n/a"
    return f"+0x{offset:X}"


def fmt_int(n: int | None) -> str:
    return "-" if n is None else f"{n:,}"


def fmt_size(n: int | None) -> str:
    """Human-oriented size: '32 KB', '1 MB', otherwise '12,416 B'."""
    if n is None:
        return "-"
    if n >= 1024 * 1024 and n % (1024 * 1024) == 0:
        return f"{n // (1024 * 1024):,} MB"
    if n >= 1024 and n % 1024 == 0:
        return f"{n // 1024:,} KB"
    return f"{n:,} B"


def fmt_pct(part: int, whole: int, digits: int = 2) -> str:
    if whole <= 0:
        return f"{0:.{digits}f}%"
    return f"{part * 100.0 / whole:.{digits}f}%"


def parse_int(value) -> int:
    """Parse an address/size given as int, '0x..' hex, '..h' hex or decimal.

    Underscores are allowed as separators (``0x0800_0000``).
    Raises ``ValueError`` with a readable message.
    """
    if isinstance(value, bool):
        raise ValueError(f"invalid number: {value!r}")
    if isinstance(value, int):
        if value < 0:
            raise ValueError(f"negative value not allowed: {value}")
        return value
    if not isinstance(value, str):
        raise ValueError(f"invalid number: {value!r}")
    text = value.strip().replace("_", "")
    if not text:
        raise ValueError("empty value")
    try:
        if text.lower().startswith("0x"):
            result = int(text[2:], 16)
        elif text.lower().endswith("h") and len(text) > 1:
            result = int(text[:-1], 16)
        else:
            result = int(text, 10)
    except ValueError:
        raise ValueError(f"invalid number: {value!r} (use 0x prefix for hexadecimal)") from None
    if result < 0:
        raise ValueError(f"negative value not allowed: {value!r}")
    return result


def parse_hex_bytes(text: str) -> bytes:
    """Parse a search pattern such as 'DE AD BE EF', 'deadbeef' or '0xDE,0xAD'."""
    cleaned = text.replace(",", " ").replace("0x", " ").replace("0X", " ").split()
    joined = "".join(cleaned)
    if not joined:
        raise ValueError("empty search pattern")
    if len(joined) % 2:
        raise ValueError("hex pattern must contain an even number of digits")
    try:
        return bytes.fromhex(joined)
    except ValueError:
        raise ValueError(f"invalid hex pattern: {text!r}") from None
