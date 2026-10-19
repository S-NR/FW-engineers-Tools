"""In-memory model of a loaded firmware image.

A firmware image is a set of non-overlapping *segments*, each mapping a range
of MCU memory addresses to bytes. A raw ``.bin`` is one segment placed at a
user-selected base address; an Intel HEX file or ELF can contain several
segments with gaps between them.

The model keeps the distinction between

* **address** – the MCU memory address the byte is programmed to, and
* **file offset** – where the byte lives inside the file on disk (only
  meaningful for BIN and ELF; Intel HEX has no byte-level file offset).
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Any, Iterator

from ..errors import FirmwareFormatError
from .intervals import IntervalSet


@dataclass
class Segment:
    start: int
    data: Any  # bytes, bytearray or read-only mmap (supports len/slicing/find)
    file_offset: int | None = None
    name: str = ""

    @property
    def end(self) -> int:
        """Exclusive end address."""
        return self.start + len(self.data)

    def __len__(self) -> int:
        return len(self.data)


@dataclass
class FirmwareImage:
    path: str
    name: str
    format: str  # 'bin' | 'hex' | 'elf'
    file_size: int
    sha256: str
    segments: list[Segment]
    warnings: list[str] = field(default_factory=list)
    info: dict[str, str] = field(default_factory=dict)  # format-specific facts
    elf: Any = None  # formats.elf_parser.ElfInfo when format == 'elf'
    base_address: int | None = None  # only for BIN
    mcu: Any = None  # core.mcu_detector.McuIdentification (heuristic controller identification)
    _closables: list = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        segs = sorted((s for s in self.segments if len(s.data) > 0), key=lambda s: s.start)
        for prev, nxt in zip(segs, segs[1:]):
            if nxt.start < prev.end:
                raise FirmwareFormatError(
                    f"{self.name}: overlapping data at 0x{nxt.start:X} "
                    f"(segment 0x{prev.start:X}-0x{prev.end - 1:X} overlaps "
                    f"0x{nxt.start:X}-0x{nxt.end - 1:X})"
                )
        self.segments = segs
        self._starts = [s.start for s in segs]
        self.coverage = IntervalSet((s.start, s.end) for s in segs)

    # ---------------------------------------------------------------- summary
    @property
    def data_size(self) -> int:
        """Number of bytes of firmware data (excluding gaps)."""
        return self.coverage.total

    @property
    def address_range(self) -> tuple[int, int] | None:
        """(first address, last address inclusive) or None for an empty image."""
        if not self.segments:
            return None
        return self.segments[0].start, self.segments[-1].end - 1

    @property
    def is_empty(self) -> bool:
        return not self.segments

    # ----------------------------------------------------------------- access
    def segment_at(self, addr: int) -> Segment | None:
        i = bisect_right(self._starts, addr) - 1
        if i >= 0 and addr < self.segments[i].end:
            return self.segments[i]
        return None

    def byte_at(self, addr: int) -> int | None:
        seg = self.segment_at(addr)
        return None if seg is None else seg.data[addr - seg.start]

    def file_offset_of(self, addr: int) -> int | None:
        seg = self.segment_at(addr)
        if seg is None or seg.file_offset is None:
            return None
        return seg.file_offset + (addr - seg.start)

    def address_of_file_offset(self, offset: int) -> int | None:
        """MCU address of the byte stored at *offset* in the file (None if not loaded data)."""
        for seg in self.segments:
            if seg.file_offset is not None and seg.file_offset <= offset < seg.file_offset + len(seg.data):
                return seg.start + (offset - seg.file_offset)
        return None

    def read(self, addr: int, length: int) -> list[int | None]:
        """Read *length* bytes; addresses without data are returned as None."""
        out: list[int | None] = [None] * length
        pos, end = addr, addr + length
        while pos < end:
            seg = self.segment_at(pos)
            if seg is not None:
                stop = min(seg.end, end)
                out[pos - addr:stop - addr] = list(seg.data[pos - seg.start:stop - seg.start])
                pos = stop
            else:
                i = bisect_right(self._starts, pos)
                pos = self._starts[i] if i < len(self._starts) and self._starts[i] < end else end
        return out

    def iter_range(self, start: int | None = None, end: int | None = None,
                   fill: int | None = None, chunk: int = 1 << 16) -> Iterator[bytes]:
        """Yield the bytes of ``[start, end)`` in address order.

        Gaps without data are skipped when *fill* is None, otherwise they are
        filled with the byte value *fill*.
        """
        if not self.segments and fill is None:
            return
        if start is None:
            start = self.segments[0].start if self.segments else 0
        if end is None:
            end = self.segments[-1].end if self.segments else start
        pos = start
        while pos < end:
            seg = self.segment_at(pos)
            if seg is not None:
                stop = min(seg.end, end, pos + chunk)
                yield bytes(seg.data[pos - seg.start:stop - seg.start])
                pos = stop
                continue
            i = bisect_right(self._starts, pos)
            nxt = self._starts[i] if i < len(self._starts) else end
            gap_end = min(nxt, end)
            if fill is not None:
                while pos < gap_end:
                    n = min(chunk, gap_end - pos)
                    yield bytes([fill]) * n
                    pos += n
            pos = gap_end

    def find(self, pattern: bytes, start_addr: int, backwards: bool = False) -> int | None:
        """Find *pattern* at/after *start_addr* (or before it when *backwards*).

        Matches spanning a gap between two segments are not reported.
        """
        if not pattern:
            return None
        if not backwards:
            for seg in self.segments:
                if seg.end <= start_addr:
                    continue
                off = max(0, start_addr - seg.start)
                idx = seg.data.find(pattern, off)
                if idx >= 0:
                    return seg.start + idx
            return None
        for seg in reversed(self.segments):
            if seg.start >= start_addr:
                continue
            limit = min(len(seg.data), start_addr - seg.start + len(pattern) - 1)
            idx = seg.data.rfind(pattern, 0, limit)
            if idx >= 0 and seg.start + idx < start_addr:
                return seg.start + idx
        return None

    # -------------------------------------------------------------- lifecycle
    def close(self) -> None:
        """Release memory maps / file handles. The original file is never modified."""
        for obj in self._closables:
            try:
                obj.close()
            except Exception:  # pragma: no cover - best effort cleanup
                pass
        self._closables.clear()
