"""Raw binary (.bin) loader.

The file is memory-mapped read-only, so large images are not copied into RAM
and the original file can never be modified.
"""

from __future__ import annotations

import mmap
import os

from ..core.image import Segment
from ..errors import FirmwareFormatError

MAX_ADDRESS = (1 << 64) - 1


def parse_bin(path: str, base_address: int = 0) -> tuple[list[Segment], list[str], dict, list]:
    """Return (segments, warnings, info, closables) for a raw binary file."""
    if base_address < 0:
        raise FirmwareFormatError("Base address must not be negative")
    size = os.path.getsize(path)
    if base_address + size - 1 > MAX_ADDRESS:
        raise FirmwareFormatError("Base address + file size exceeds the 64-bit address space")
    info = {"Base address": f"0x{base_address:08X}"}
    if size == 0:
        return [], ["File is empty (0 bytes) - every byte of the other image will be reported as a difference."], info, []
    with open(path, "rb") as f:
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
    return [Segment(base_address, mm, 0, "file")], [], info, [mm]
