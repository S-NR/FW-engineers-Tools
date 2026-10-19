"""Simple (non-CRC) checksums used by small embedded protocols."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from core.crc import CRCMatch

__all__ = ["ChecksumAlgorithm", "CHECKSUMS", "checksum_names", "get_checksum", "compute_checksum",
           "find_matching_checksum", "xor8", "sum8", "sum16", "twos_complement8", "ones_complement16",
           "fletcher16"]


def xor8(data: bytes) -> int:
    """XOR of all bytes (LRC used by many UART protocols, NMEA)."""
    result = 0
    for b in data:
        result ^= b
    return result


def sum8(data: bytes) -> int:
    """Arithmetic sum of all bytes, modulo 256."""
    return sum(data) & 0xFF


def sum16(data: bytes) -> int:
    """Arithmetic sum of all bytes, modulo 65536."""
    return sum(data) & 0xFFFF


def twos_complement8(data: bytes) -> int:
    """8-bit two's-complement checksum (Intel HEX, Modbus ASCII LRC): sum of data + checksum == 0."""
    return (-sum(data)) & 0xFF


def ones_complement16(data: bytes) -> int:
    """16-bit one's-complement checksum of big-endian words (RFC 1071 / IP header)."""
    if len(data) % 2:
        data = data + b"\x00"
    total = 0
    for i in range(0, len(data), 2):
        total += (data[i] << 8) | data[i + 1]
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def fletcher16(data: bytes) -> int:
    s1 = s2 = 0
    for b in data:
        s1 = (s1 + b) % 255
        s2 = (s2 + s1) % 255
    return (s2 << 8) | s1


@dataclass(frozen=True)
class ChecksumAlgorithm:
    name: str
    func: Callable[[bytes], int]
    width: int  # bits
    description: str

    @property
    def byte_length(self) -> int:
        return self.width // 8


CHECKSUMS: dict[str, ChecksumAlgorithm] = {
    a.name: a
    for a in [
        ChecksumAlgorithm("xor8", xor8, 8, "XOR checksum (LRC) of all bytes"),
        ChecksumAlgorithm("sum8", sum8, 8, "SUM checksum: byte sum modulo 256"),
        ChecksumAlgorithm("sum16", sum16, 16, "16-bit SUM checksum: byte sum modulo 65536"),
        ChecksumAlgorithm("twos_complement8", twos_complement8, 8,
                          "8-bit checksum: two's complement of byte sum (sum + checksum = 0)"),
        ChecksumAlgorithm("ones_complement16", ones_complement16, 16,
                          "16-bit checksum: one's complement sum of 16-bit words (RFC 1071)"),
        ChecksumAlgorithm("fletcher16", fletcher16, 16, "Fletcher-16"),
    ]
}

_ALIASES = {
    "xor": "xor8", "lrc": "xor8", "sum": "sum8", "checksum8": "twos_complement8",
    "8bit": "twos_complement8", "checksum16": "ones_complement16", "16bit": "ones_complement16",
    "internet": "ones_complement16",
}


def checksum_names() -> list[str]:
    return list(CHECKSUMS)


def get_checksum(name: str) -> ChecksumAlgorithm:
    key = str(name).strip().lower().replace("-", "_").replace(" ", "")
    key = _ALIASES.get(key, key)
    if key not in CHECKSUMS:
        raise ValueError(f"Unknown checksum algorithm '{name}'. Known: {', '.join(CHECKSUMS)}")
    return CHECKSUMS[key]


def compute_checksum(name: str, data: bytes) -> int:
    return get_checksum(name).func(bytes(data))


def find_matching_checksum(packets: list[bytes], max_start: int = 4,
                           trailer_lengths: Iterable[int] = (0, 1, 2)) -> list[CRCMatch]:
    """Like :func:`core.crc.find_matching_crc`, for the simple checksums.

    Requires at least two packets: a single 8-bit checksum matches by chance too often.
    """
    packets = [bytes(p) for p in packets if p]
    if len(packets) < 2:
        return []
    matches: list[CRCMatch] = []
    for alg in CHECKSUMS.values():
        n = alg.byte_length
        for trailer in trailer_lengths:
            for start in range(max_start + 1):
                for order in (("big",) if n == 1 else ("little", "big")):
                    if all(_checksum_matches(p, alg, start, trailer, order) for p in packets):
                        off = len(packets[0]) - trailer - n
                        matches.append(CRCMatch(alg.name, start, off - 1, off, order, n))
    return matches


def _checksum_matches(pkt: bytes, alg: ChecksumAlgorithm, start: int, trailer: int, order: str) -> bool:
    off = len(pkt) - trailer - alg.byte_length
    if off - start < 1:
        return False
    return pkt[off:off + alg.byte_length] == alg.func(pkt[start:off]).to_bytes(alg.byte_length, order)
