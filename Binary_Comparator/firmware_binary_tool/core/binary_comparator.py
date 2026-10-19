"""Address-based byte-by-byte comparison of two firmware images.

The comparison runs over the *union* of the address ranges of both images:

* addresses present in both images are compared byte-by-byte;
* addresses present in only one image are reported as differences
  (``ONLY_A`` / ``ONLY_B``) - a size mismatch is never silently ignored.

The engine works in 64 KiB chunks and narrows down differing chunks
hierarchically (4096 -> 256 -> 16 -> 1 bytes), so identical areas are skipped
at ``memcmp`` speed and only differing bytes are inspected individually.
Differences are stored as runs (:class:`IntervalSet`), not per byte.
"""

from __future__ import annotations

import time
from array import array
from dataclasses import dataclass, field
from typing import Callable

from ..errors import ComparisonCancelled
from .image import FirmwareImage
from .intervals import IntervalSet

CHUNK = 1 << 16

SAME, DIFF, ONLY_A, ONLY_B, NO_DATA = "SAME", "DIFF", "ONLY A", "ONLY B", "NO DATA"

ProgressFn = Callable[[int, int], None]


class _RunBuilder:
    """Accumulates sorted differing runs, merging adjacent ones."""

    __slots__ = ("starts", "ends")

    def __init__(self) -> None:
        self.starts = array("Q")
        self.ends = array("Q")

    def add(self, s: int, e: int) -> None:
        if self.ends and self.ends[-1] == s:
            self.ends[-1] = e
        else:
            self.starts.append(s)
            self.ends.append(e)


def _scan_chunk(ca, cb, base: int, runs: _RunBuilder) -> None:
    """Record differing runs between two equal-length byte chunks."""
    n = len(ca)
    for i in range(0, n, 4096):
        a1, b1 = ca[i:i + 4096], cb[i:i + 4096]
        if a1 == b1:
            continue
        for j in range(0, len(a1), 256):
            a2, b2 = a1[j:j + 256], b1[j:j + 256]
            if a2 == b2:
                continue
            for k in range(0, len(a2), 16):
                a3, b3 = a2[k:k + 16], b2[k:k + 16]
                if a3 == b3:
                    continue
                off = base + i + j + k
                run_start = -1
                for m in range(len(a3)):
                    if a3[m] != b3[m]:
                        if run_start < 0:
                            run_start = m
                    elif run_start >= 0:
                        runs.add(off + run_start, off + m)
                        run_start = -1
                if run_start >= 0:
                    runs.add(off + run_start, off + len(a3))


def diff_buffers(a, b, base: int = 0) -> IntervalSet:
    """Differing runs between two buffers (addresses relative to *base*).

    Bytes beyond the shorter buffer are reported as differing.
    """
    runs = _RunBuilder()
    n = min(len(a), len(b))
    for pos in range(0, n, CHUNK):
        ca, cb = a[pos:min(pos + CHUNK, n)], b[pos:min(pos + CHUNK, n)]
        if ca != cb:
            _scan_chunk(ca, cb, base + pos, runs)
    if len(a) != len(b):
        runs.add(base + n, base + max(len(a), len(b)))
    return IntervalSet.from_arrays(runs.starts, runs.ends)


def count_diff_bytes(a, b) -> int:
    return diff_buffers(a, b).total


@dataclass
class DiffResult:
    """Raw comparison result. Independent of any region configuration."""

    image_a: FirmwareImage
    image_b: FirmwareImage
    coverage_a: IntervalSet
    coverage_b: IntervalSet
    both: IntervalSet  # addresses present in both images
    only_a: IntervalSet  # present only in A (and != fill byte when a fill is used)
    only_b: IntervalSet  # present only in B (and != fill byte when a fill is used)
    content_diff: IntervalSet  # present in both but different
    diff: IntervalSet  # every differing address (content_diff | only_a | only_b)
    union: IntervalSet  # every address present in at least one image
    elapsed: float = 0.0
    fill: int | None = None  # gap fill byte used for one-sided data (None = strict)
    absent_b: IntervalSet = field(default_factory=IntervalSet)  # data in A, no data in B
    absent_a: IntervalSet = field(default_factory=IntervalSet)  # data in B, no data in A

    @property
    def total_compared(self) -> int:
        """Bytes compared: present in both, plus one-sided bytes compared against the fill byte."""
        if self.fill is None:
            return self.both.total
        return self.union.total

    @property
    def union_bytes(self) -> int:
        return self.union.total

    @property
    def diff_bytes(self) -> int:
        return self.diff.total

    @property
    def content_diff_bytes(self) -> int:
        return self.content_diff.total

    @property
    def only_a_bytes(self) -> int:
        return self.only_a.total

    @property
    def only_b_bytes(self) -> int:
        return self.only_b.total

    @property
    def changed_percent(self) -> float:
        return self.diff_bytes * 100.0 / self.union_bytes if self.union_bytes else 0.0

    @property
    def identical(self) -> bool:
        return self.diff_bytes == 0

    @property
    def first_diff(self) -> int | None:
        return self.diff.first

    @property
    def last_diff(self) -> int | None:
        return self.diff.last

    def status_at(self, addr: int) -> str:
        if self.only_a.contains(addr):
            return ONLY_A
        if self.only_b.contains(addr):
            return ONLY_B
        if self.content_diff.contains(addr):
            return DIFF
        if self.both.contains(addr):
            return SAME
        if self.fill is not None and (self.absent_a.contains(addr) or self.absent_b.contains(addr)):
            return SAME_FILL
        return NO_DATA


SAME_FILL = "SAME (fill)"


def _non_fill_runs(img: FirmwareImage, intervals: IntervalSet, fill: int, cancel=None) -> IntervalSet:
    """Addresses inside *intervals* whose byte in *img* differs from *fill*."""
    runs = _RunBuilder()
    for s, e in intervals:
        pos = s
        while pos < e:
            seg = img.segment_at(pos)
            stop = min(e, seg.end, pos + CHUNK)
            chunk = seg.data[pos - seg.start:stop - seg.start]
            ref = bytes([fill]) * len(chunk)
            if chunk != ref:
                _scan_chunk(chunk, ref, pos, runs)
            pos = stop
            if cancel is not None and cancel.is_set():
                raise ComparisonCancelled("Comparison cancelled by user")
    return IntervalSet.from_arrays(runs.starts, runs.ends)


def compare_images(a: FirmwareImage, b: FirmwareImage, progress: ProgressFn | None = None,
                   cancel=None, fill: int | None = None) -> DiffResult:
    """Compare two images over the union of their address ranges.

    *progress* is called as ``progress(done_bytes, total_bytes)``; *cancel* is
    an optional ``threading.Event`` that aborts with :class:`ComparisonCancelled`.

    By default (``fill=None``) every byte present in only one image is a
    difference. With ``fill=0xFF`` such bytes are instead compared against the
    fill value (erased flash), which is useful when comparing a sparse HEX file
    with a full BIN dump. This must be requested explicitly.
    """
    if fill is not None and not 0 <= fill <= 0xFF:
        raise ValueError("fill byte must be in range 0x00-0xFF")
    t0 = time.perf_counter()
    cov_a, cov_b = a.coverage, b.coverage
    both = cov_a.intersect(cov_b)
    absent_b = cov_a.subtract(cov_b)
    absent_a = cov_b.subtract(cov_a)
    if fill is None:
        only_a, only_b = absent_b, absent_a
    else:
        only_a = _non_fill_runs(a, absent_b, fill, cancel)
        only_b = _non_fill_runs(b, absent_a, fill, cancel)
    runs = _RunBuilder()
    total = both.total
    done = 0
    if progress:
        progress(0, total)
    for s, e in both:
        pos = s
        while pos < e:
            sa, sb = a.segment_at(pos), b.segment_at(pos)
            stop = min(e, sa.end, sb.end)
            oa, ob = pos - sa.start, pos - sb.start
            length = stop - pos
            p = 0
            while p < length:
                n = min(CHUNK, length - p)
                ca = sa.data[oa + p:oa + p + n]
                cb = sb.data[ob + p:ob + p + n]
                if ca != cb:
                    _scan_chunk(ca, cb, pos + p, runs)
                p += n
                done += n
                if cancel is not None and cancel.is_set():
                    raise ComparisonCancelled("Comparison cancelled by user")
                if progress:
                    progress(done, total)
            pos = stop
    content = IntervalSet.from_arrays(runs.starts, runs.ends)
    diff = content.union(only_a).union(only_b)
    return DiffResult(
        image_a=a, image_b=b, coverage_a=cov_a, coverage_b=cov_b, both=both,
        only_a=only_a, only_b=only_b, content_diff=content, diff=diff,
        union=cov_a.union(cov_b), elapsed=time.perf_counter() - t0,
        fill=fill, absent_b=absent_b, absent_a=absent_a,
    )
