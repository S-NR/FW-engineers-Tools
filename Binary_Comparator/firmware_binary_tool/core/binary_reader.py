"""Firmware file loading: format detection, hashing and dispatch to parsers.

Files are always opened read-only. Raw binaries are memory-mapped so large
images are not read entirely into RAM.
"""

from __future__ import annotations

import hashlib
import os

from ..errors import FirmwareFormatError, FirmwareToolError
from .image import FirmwareImage

FORMATS = ("auto", "bin", "hex", "elf")
HEX_EXTENSIONS = {".hex", ".ihex", ".ihx", ".h86"}
ELF_EXTENSIONS = {".elf", ".axf", ".out"}
_HEX_TEXT = set(b"0123456789abcdefABCDEF:\r\n \t")


def sha256_file(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def detect_format(path: str) -> str:
    """Guess the file format from magic bytes, extension and content."""
    with open(path, "rb") as f:
        head = f.read(4096)
    if head[:4] == b"\x7fELF":
        return "elf"
    ext = os.path.splitext(path)[1].lower()
    if ext in ELF_EXTENSIONS:
        return "elf"  # parser will report a clear error if the magic is wrong
    if ext in HEX_EXTENSIONS:
        return "hex"
    stripped = head.lstrip()
    if stripped[:1] == b":" and set(head) <= _HEX_TEXT:
        return "hex"
    return "bin"


def load_firmware(path: str, fmt: str = "auto", base_address: int = 0,
                  elf_use_vaddr: bool = False) -> FirmwareImage:
    """Load a firmware file into a :class:`FirmwareImage`.

    ``base_address`` is the MCU address of file offset 0 for raw binaries; it
    is ignored for HEX and ELF which carry their own addresses.
    """
    fmt = (fmt or "auto").lower()
    if fmt not in FORMATS:
        raise FirmwareToolError(f"Unknown format '{fmt}' (expected one of {', '.join(FORMATS)})")
    if not os.path.exists(path):
        raise FirmwareToolError(f"File not found: {path}")
    if not os.path.isfile(path):
        raise FirmwareToolError(f"Not a regular file: {path}")
    try:
        file_size = os.path.getsize(path)
        if fmt == "auto":
            fmt = detect_format(path)
        digest = sha256_file(path)
    except OSError as exc:
        raise FirmwareToolError(f"Cannot read {path}: {exc}") from exc

    name = os.path.basename(path)
    try:
        if fmt == "bin":
            from ..formats.bin_parser import parse_bin
            segs, warns, info, closables = parse_bin(path, base_address)
            img = FirmwareImage(path, name, "bin", file_size, digest, segs, warns, info,
                                base_address=base_address, _closables=closables)
            return _identify(img)
        if fmt == "hex":
            from ..formats.hex_parser import parse_intel_hex
            res = parse_intel_hex(path)
            return _identify(FirmwareImage(path, name, "hex", file_size, digest, res.segments, res.warnings,
                                           res.info))
        from ..formats.elf_parser import load_segments, parse_elf
        elf = parse_elf(path)
        segs, warns = load_segments(elf, elf_use_vaddr)
        info = elf.summary()
        info["Address mode"] = "VMA (virtual)" if elf_use_vaddr else "LMA (load / flash)"
        return _identify(FirmwareImage(path, name, "elf", file_size, digest, segs, elf.warnings + warns, info,
                                       elf=elf))
    except FirmwareFormatError as exc:
        raise FirmwareFormatError(f"{name}: {exc}" if not str(exc).startswith(name) else str(exc)) from None
    except (OSError, ValueError) as exc:
        raise FirmwareFormatError(f"{name}: cannot be loaded as {fmt.upper()}: {exc}") from exc


def _identify(img: FirmwareImage, extra_files=()) -> FirmwareImage:
    """Attach the heuristic controller identification; never fails the load."""
    from .mcu_detector import detect_mcu
    try:
        img.mcu = detect_mcu(img, extra_files)
    except Exception as exc:  # identification is auxiliary
        img.warnings.append(f"Controller identification failed: {exc}")
    return img


def identify_with_symbols(img: FirmwareImage, symbol_path: str | None) -> None:
    """Re-run controller identification including a symbol file (ELF / .map) of the same build."""
    _identify(img, [symbol_path] if symbol_path and symbol_path != img.path else [])
