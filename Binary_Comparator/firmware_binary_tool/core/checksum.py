"""Optional checksum calculation (CRC-32, CRC-16 variants, SHA-256).

These are *informational* checksums computed by the tool. They do not verify
the firmware's own embedded CRC unless the user deliberately selects the same
algorithm and range that the firmware uses.
"""

from __future__ import annotations

import binascii
import hashlib
import zlib
from typing import Iterable


def _make_reflected_table(poly: int) -> list[int]:
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            crc = (crc >> 1) ^ poly if crc & 1 else crc >> 1
        table.append(crc)
    return table


_TABLE_A001 = _make_reflected_table(0xA001)


class _Crc32:
    width = 8

    def __init__(self):
        self.value = 0

    def update(self, data: bytes) -> None:
        self.value = zlib.crc32(data, self.value)

    def result(self) -> str:
        return f"0x{self.value & 0xFFFFFFFF:08X}"


class _CrcHqx:
    """CRC-16 with polynomial 0x1021, non-reflected (CCITT-FALSE / XMODEM)."""

    def __init__(self, init: int):
        self.value = init

    def update(self, data: bytes) -> None:
        self.value = binascii.crc_hqx(data, self.value)

    def result(self) -> str:
        return f"0x{self.value:04X}"


class _CrcReflected16:
    """CRC-16 with reflected polynomial 0xA001 (MODBUS / ARC)."""

    def __init__(self, init: int):
        self.value = init

    def update(self, data: bytes) -> None:
        crc, table = self.value, _TABLE_A001
        for byte in data:
            crc = (crc >> 8) ^ table[(crc ^ byte) & 0xFF]
        self.value = crc

    def result(self) -> str:
        return f"0x{self.value:04X}"


class _Sha256:
    def __init__(self):
        self.h = hashlib.sha256()

    def update(self, data: bytes) -> None:
        self.h.update(data)

    def result(self) -> str:
        return self.h.hexdigest()


ALGORITHMS = {
    "CRC-32": (lambda: _Crc32(), "CRC-32/ISO-HDLC (zlib): poly 0x04C11DB7, init 0xFFFFFFFF, reflected, xorout 0xFFFFFFFF"),
    "CRC-16/CCITT-FALSE": (lambda: _CrcHqx(0xFFFF), "poly 0x1021, init 0xFFFF, not reflected, xorout 0x0000"),
    "CRC-16/XMODEM": (lambda: _CrcHqx(0x0000), "poly 0x1021, init 0x0000, not reflected, xorout 0x0000"),
    "CRC-16/MODBUS": (lambda: _CrcReflected16(0xFFFF), "poly 0x8005, init 0xFFFF, reflected, xorout 0x0000"),
    "CRC-16/ARC": (lambda: _CrcReflected16(0x0000), "poly 0x8005, init 0x0000, reflected, xorout 0x0000"),
    "SHA-256": (lambda: _Sha256(), "SHA-256 (FIPS 180-4)"),
}


def algorithm_names() -> list[str]:
    return list(ALGORITHMS)


def describe(algorithm: str) -> str:
    return ALGORITHMS[algorithm][1]


def compute(algorithm: str, chunks: Iterable[bytes]) -> str:
    try:
        calc = ALGORITHMS[algorithm][0]()
    except KeyError:
        raise ValueError(f"Unknown checksum algorithm '{algorithm}'. "
                         f"Available: {', '.join(ALGORITHMS)}") from None
    for chunk in chunks:
        calc.update(chunk)
    return calc.result()


def checksum_bytes(algorithm: str, data: bytes) -> str:
    return compute(algorithm, [data])


def checksum_file(algorithm: str, path: str, chunk: int = 1 << 20) -> str:
    """Checksum of the file exactly as stored on disk."""
    def gen():
        with open(path, "rb") as f:
            while True:
                block = f.read(chunk)
                if not block:
                    return
                yield block
    return compute(algorithm, gen())


def checksum_image(algorithm: str, image, start: int | None = None, end: int | None = None,
                   fill: int | None = None) -> str:
    """Checksum over image data in address order.

    ``end`` is exclusive. With ``fill=None`` gaps without data are skipped,
    otherwise they are filled with the given byte value (typically 0xFF for
    erased flash).
    """
    return compute(algorithm, image.iter_range(start, end, fill))
