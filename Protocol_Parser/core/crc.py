"""Parameterised CRC engine (Rocksoft/Williams model).

Every CRC is described by ``width, poly, init, refin, refout, xorout``. Named
presets cover the common embedded variants; any other CRC can be described
with custom parameters. Each preset carries the standard "check" value (CRC of
ASCII ``"123456789"``) which the unit tests verify.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any, Iterable

from utils.hex_utils import parse_int

__all__ = [
    "CRCParams",
    "CRCError",
    "PRESETS",
    "preset_names",
    "get_preset",
    "resolve_crc",
    "compute_crc",
    "crc_to_bytes",
    "CRCMatch",
    "find_matching_crc",
]


class CRCError(ValueError):
    """Invalid CRC configuration."""


@dataclass(frozen=True)
class CRCParams:
    name: str
    width: int
    poly: int
    init: int
    refin: bool
    refout: bool
    xorout: int
    check: int | None = None
    note: str = ""

    @property
    def byte_length(self) -> int:
        return (self.width + 7) // 8

    def validate(self) -> None:
        if not 8 <= self.width <= 64:
            raise CRCError(f"CRC width must be between 8 and 64 bits (got {self.width})")
        mask = (1 << self.width) - 1
        for label, value in (("polynomial", self.poly), ("initial value", self.init), ("final XOR", self.xorout)):
            if not 0 <= value <= mask:
                raise CRCError(f"CRC {label} 0x{value:X} does not fit in {self.width} bits")
        if self.poly == 0:
            raise CRCError("CRC polynomial must not be zero")

    def describe(self) -> str:
        digits = (self.width + 3) // 4
        return (
            f"{self.name}: width={self.width} poly=0x{self.poly:0{digits}X} init=0x{self.init:0{digits}X} "
            f"refin={self.refin} refout={self.refout} xorout=0x{self.xorout:0{digits}X}"
        )


_PRESET_LIST = [
    CRCParams("CRC-8", 8, 0x07, 0x00, False, False, 0x00, 0xF4, "CRC-8/SMBUS"),
    CRCParams("CRC-8/MAXIM", 8, 0x31, 0x00, True, True, 0x00, 0xA1, "Dallas/Maxim 1-Wire"),
    CRCParams("CRC-8/AUTOSAR", 8, 0x2F, 0xFF, False, False, 0xFF, 0xDF),
    CRCParams("CRC-16", 16, 0x8005, 0x0000, True, True, 0x0000, 0xBB3D, "CRC-16/ARC (IBM)"),
    CRCParams("CRC-16/CCITT", 16, 0x1021, 0xFFFF, False, False, 0x0000, 0x29B1, "CRC-16/CCITT-FALSE, IBM-3740"),
    CRCParams("CRC-16/MODBUS", 16, 0x8005, 0xFFFF, True, True, 0x0000, 0x4B37, "Modbus RTU"),
    CRCParams("CRC-16/XMODEM", 16, 0x1021, 0x0000, False, False, 0x0000, 0x31C3, "XMODEM, ZMODEM, ACORN"),
    CRCParams("CRC-16/KERMIT", 16, 0x1021, 0x0000, True, True, 0x0000, 0x2189, "CCITT true, KERMIT"),
    CRCParams("CRC-16/X-25", 16, 0x1021, 0xFFFF, True, True, 0xFFFF, 0x906E, "IBM-SDLC, HDLC"),
    CRCParams("CRC-16/AUG-CCITT", 16, 0x1021, 0x1D0F, False, False, 0x0000, 0xE5CC, "SPI-FUJITSU"),
    CRCParams("CRC-16/USB", 16, 0x8005, 0xFFFF, True, True, 0xFFFF, 0xB4C8),
    CRCParams("CRC-16/MAXIM", 16, 0x8005, 0x0000, True, True, 0xFFFF, 0x44C2),
    CRCParams("CRC-16/DNP", 16, 0x3D65, 0x0000, True, True, 0xFFFF, 0xEA82),
    CRCParams("CRC-32", 32, 0x04C11DB7, 0xFFFFFFFF, True, True, 0xFFFFFFFF, 0xCBF43926, "ISO-HDLC, Ethernet, zlib"),
    CRCParams("CRC-32C", 32, 0x1EDC6F41, 0xFFFFFFFF, True, True, 0xFFFFFFFF, 0xE3069283, "Castagnoli, iSCSI"),
    CRCParams("CRC-32/MPEG-2", 32, 0x04C11DB7, 0xFFFFFFFF, False, False, 0x00000000, 0x0376E6E7, "STM32 hardware CRC unit default"),
]

PRESETS: dict[str, CRCParams] = {p.name: p for p in _PRESET_LIST}

_ALIASES = {
    "CRC8": "CRC-8",
    "CRC-8/SMBUS": "CRC-8",
    "CRC16": "CRC-16",
    "CRC-16/ARC": "CRC-16",
    "ARC": "CRC-16",
    "CRC-16/CCITT-FALSE": "CRC-16/CCITT",
    "CRC-16/IBM-3740": "CRC-16/CCITT",
    "CCITT": "CRC-16/CCITT",
    "CRC16/CCITT": "CRC-16/CCITT",
    "CRC16/MODBUS": "CRC-16/MODBUS",
    "CRC16MODBUS": "CRC-16/MODBUS",
    "MODBUS": "CRC-16/MODBUS",
    "XMODEM": "CRC-16/XMODEM",
    "KERMIT": "CRC-16/KERMIT",
    "CRC-16/X25": "CRC-16/X-25",
    "CRC32": "CRC-32",
    "CRC-32/ISO-HDLC": "CRC-32",
    "CRC32C": "CRC-32C",
}


def _norm(name: str) -> str:
    return str(name).strip().upper().replace("_", "-").replace(" ", "")


def preset_names() -> list[str]:
    return list(PRESETS)


def get_preset(name: str) -> CRCParams:
    key = _norm(name)
    for preset_name in PRESETS:
        if _norm(preset_name) == key:
            return PRESETS[preset_name]
    alias = _ALIASES.get(key)
    if alias:
        return PRESETS[alias]
    raise CRCError(f"Unknown CRC algorithm '{name}'. Known: {', '.join(PRESETS)}")


def resolve_crc(config: dict[str, Any] | None) -> CRCParams:
    """Build CRC parameters from a protocol-definition dict.

    ``{"algorithm": "CRC-16/MODBUS"}`` selects a preset. Any of ``width, poly,
    init, xorout, refin, refout`` override preset values; with
    ``"algorithm": "custom"`` all parameters must be given.
    """
    config = config or {}
    algorithm = str(config.get("algorithm") or "CRC-16")
    try:
        if algorithm.lower() == "custom":
            missing = [k for k in ("width", "poly") if config.get(k) in (None, "")]
            if missing:
                raise CRCError(f"Custom CRC requires: {', '.join(missing)}")
            params = CRCParams("custom", 16, 0x1021, 0, False, False, 0)
        else:
            params = get_preset(algorithm)

        overrides: dict[str, Any] = {}
        for key in ("width", "poly", "init", "xorout"):
            if config.get(key) not in (None, ""):
                overrides[key] = parse_int(config[key])
        for key in ("refin", "refout"):
            if key in config and config[key] is not None:
                overrides[key] = _as_bool(config[key])
    except ValueError as exc:
        raise CRCError(str(exc)) from exc

    if overrides:
        changed = any(getattr(params, k) != v for k, v in overrides.items())
        params = replace(params, **overrides)
        if changed and params.name != "custom":
            params = replace(params, name=f"{params.name} (modified)", check=None)
    params.validate()
    return params


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _reflect(value: int, width: int) -> int:
    result = 0
    for _ in range(width):
        result = (result << 1) | (value & 1)
        value >>= 1
    return result


@lru_cache(maxsize=64)
def _table(width: int, poly: int, reflected: bool) -> tuple[int, ...]:
    mask = (1 << width) - 1
    table = []
    if reflected:
        rpoly = _reflect(poly, width)
        for i in range(256):
            crc = i
            for _ in range(8):
                crc = (crc >> 1) ^ rpoly if crc & 1 else crc >> 1
            table.append(crc & mask)
    else:
        top = 1 << (width - 1)
        for i in range(256):
            crc = i << (width - 8)
            for _ in range(8):
                crc = ((crc << 1) ^ poly) if crc & top else (crc << 1)
            table.append(crc & mask)
    return tuple(table)


def compute_crc(data: bytes | Iterable[int], params: CRCParams | str) -> int:
    """Compute a CRC over ``data``. ``params`` may be a preset name."""
    if isinstance(params, str):
        params = get_preset(params)
    width = params.width
    mask = (1 << width) - 1
    table = _table(width, params.poly, params.refin)

    if params.refin:
        crc = _reflect(params.init, width)
        for b in bytes(data):
            crc = (crc >> 8) ^ table[(crc ^ b) & 0xFF]
        if not params.refout:
            crc = _reflect(crc, width)
    else:
        crc = params.init
        shift = width - 8
        for b in bytes(data):
            crc = ((crc << 8) & mask) ^ table[((crc >> shift) ^ b) & 0xFF]
        if params.refout:
            crc = _reflect(crc, width)
    return (crc ^ params.xorout) & mask


def crc_to_bytes(value: int, width: int, byte_order: str = "big") -> bytes:
    return value.to_bytes((width + 7) // 8, "little" if byte_order == "little" else "big")


@dataclass(frozen=True)
class CRCMatch:
    algorithm: str
    range_start: int  # inclusive offset of first covered byte
    range_end: int  # inclusive offset of last covered byte
    crc_offset: int
    byte_order: str
    size: int = 2  # bytes

    def describe(self) -> str:
        where = f"offset {self.crc_offset}" if self.size == 1 else             f"offsets {self.crc_offset}-{self.crc_offset + self.size - 1} ({self.byte_order}-endian)"
        return f"{self.algorithm} over offsets {self.range_start}-{self.range_end}, stored at {where}"


def find_matching_crc(
    packets: list[bytes],
    max_start: int = 4,
    trailer_lengths: Iterable[int] = (0, 1, 2),
    presets: Iterable[str] | None = None,
) -> list[CRCMatch]:
    """Search presets for a CRC placement consistent with *every* packet.

    The CRC is assumed to sit just before ``trailer`` bytes at the end of the
    packet (e.g. trailer 1 = an ETX byte) and to cover bytes from ``start``
    up to the byte before the CRC. Only exact matches across all packets are
    returned, so results are computed facts, not guesses - but with very few
    packets, especially for 8-bit CRCs, coincidental matches are possible.
    """
    packets = [bytes(p) for p in packets if p]
    if not packets:
        return []
    names = list(presets) if presets else preset_names()
    matches: list[CRCMatch] = []
    for name in names:
        params = PRESETS[name]
        n_crc = params.byte_length
        for trailer in trailer_lengths:
            for start in range(max_start + 1):
                for order in ("little", "big"):
                    ok = True
                    for pkt in packets:
                        crc_off = len(pkt) - trailer - n_crc
                        if crc_off - start < 1:
                            ok = False
                            break
                        expected = crc_to_bytes(compute_crc(pkt[start:crc_off], params), params.width, order)
                        if pkt[crc_off : crc_off + n_crc] != expected:
                            ok = False
                            break
                    if ok:
                        ref = packets[0]
                        crc_off = len(ref) - trailer - n_crc
                        matches.append(CRCMatch(name, start, crc_off - 1, crc_off, order, n_crc))
                        if n_crc == 1:
                            break  # byte order is meaningless for 1-byte CRCs
    return matches
