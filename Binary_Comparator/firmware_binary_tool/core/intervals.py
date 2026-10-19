"""Sorted, disjoint half-open address interval sets.

Every set of addresses used by the engine (file coverage, differing bytes,
expected areas, regions) is represented as an :class:`IntervalSet`. Storing
ranges instead of individual bytes keeps memory proportional to the number of
changed *runs* rather than the number of changed bytes, and allows O(log n)
"how many bytes of X fall inside [s, e)" queries.

All intervals are half-open: ``[start, end)``.
"""

from __future__ import annotations

from array import array
from bisect import bisect_left, bisect_right
from typing import Iterable, Iterator


def merge_intervals(intervals: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    """Sort and merge overlapping or adjacent half-open intervals.

    Empty or inverted intervals are dropped.
    """
    out: list[list[int]] = []
    for s, e in sorted(intervals):
        if e <= s:
            continue
        if out and s <= out[-1][1]:
            if e > out[-1][1]:
                out[-1][1] = e
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


class IntervalSet:
    """Immutable set of sorted, disjoint, non-adjacent ``[start, end)`` ranges."""

    __slots__ = ("starts", "ends", "_prefix")

    def __init__(self, intervals: Iterable[tuple[int, int]] = ()):
        merged = merge_intervals(intervals)
        self._init_arrays(array("Q", (s for s, _ in merged)), array("Q", (e for _, e in merged)))

    @classmethod
    def from_arrays(cls, starts: array, ends: array) -> "IntervalSet":
        """Build from arrays that are already sorted, disjoint and non-adjacent."""
        obj = cls.__new__(cls)
        obj._init_arrays(starts, ends)
        return obj

    def _init_arrays(self, starts: array, ends: array) -> None:
        self.starts = starts
        self.ends = ends
        prefix = array("Q", [0])
        acc = 0
        for s, e in zip(starts, ends):
            acc += e - s
            prefix.append(acc)
        self._prefix = prefix

    # ------------------------------------------------------------------ basics
    def __len__(self) -> int:
        return len(self.starts)

    def __iter__(self) -> Iterator[tuple[int, int]]:
        return zip(self.starts, self.ends)

    def __bool__(self) -> bool:
        return len(self.starts) > 0

    def __repr__(self) -> str:
        items = ", ".join(f"[0x{s:X},0x{e:X})" for s, e in list(self)[:8])
        more = "" if len(self) <= 8 else f", ... ({len(self)} total)"
        return f"IntervalSet({items}{more})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, IntervalSet):
            return NotImplemented
        return self.starts == other.starts and self.ends == other.ends

    @property
    def total(self) -> int:
        """Total number of addresses (bytes) contained in the set."""
        return self._prefix[-1]

    @property
    def first(self) -> int | None:
        return self.starts[0] if self.starts else None

    @property
    def last(self) -> int | None:
        """Last contained address (inclusive)."""
        return self.ends[-1] - 1 if self.ends else None

    def interval(self, index: int) -> tuple[int, int]:
        return self.starts[index], self.ends[index]

    # ----------------------------------------------------------------- queries
    def contains(self, addr: int) -> bool:
        i = bisect_right(self.starts, addr) - 1
        return i >= 0 and addr < self.ends[i]

    def index_containing(self, addr: int) -> int:
        """Index of the interval containing *addr*, or -1."""
        i = bisect_right(self.starts, addr) - 1
        return i if i >= 0 and addr < self.ends[i] else -1

    def overlap(self, start: int, end: int) -> int:
        """Number of addresses of the set that fall inside ``[start, end)``."""
        if end <= start or not self.starts:
            return 0
        i = bisect_right(self.ends, start)  # first interval ending after start
        j = bisect_left(self.starts, end)  # first interval starting at/after end
        if i >= j:
            return 0
        total = self._prefix[j] - self._prefix[i]
        if self.starts[i] < start:
            total -= start - self.starts[i]
        if self.ends[j - 1] > end:
            total -= self.ends[j - 1] - end
        return total

    def clip(self, start: int, end: int) -> Iterator[tuple[int, int]]:
        """Yield the parts of the set that fall inside ``[start, end)``."""
        if end <= start:
            return
        i = bisect_right(self.ends, start)
        n = len(self.starts)
        while i < n and self.starts[i] < end:
            yield max(self.starts[i], start), min(self.ends[i], end)
            i += 1

    def next_start_after(self, addr: int) -> int | None:
        """Start of the first interval that begins strictly after *addr*."""
        i = bisect_right(self.starts, addr)
        return self.starts[i] if i < len(self.starts) else None

    def prev_start_before(self, addr: int) -> int | None:
        """Start of the last interval that begins strictly before *addr*."""
        i = bisect_left(self.starts, addr) - 1
        return self.starts[i] if i >= 0 else None

    def first_at_or_after(self, addr: int) -> int | None:
        """Smallest contained address >= *addr*."""
        i = bisect_right(self.ends, addr)
        if i >= len(self.starts):
            return None
        return max(addr, self.starts[i])

    # -------------------------------------------------------------- set algebra
    def intersect(self, other: "IntervalSet") -> "IntervalSet":
        a_s, a_e, b_s, b_e = self.starts, self.ends, other.starts, other.ends
        out_s, out_e = array("Q"), array("Q")
        i = j = 0
        while i < len(a_s) and j < len(b_s):
            s = max(a_s[i], b_s[j])
            e = min(a_e[i], b_e[j])
            if s < e:
                out_s.append(s)
                out_e.append(e)
            if a_e[i] < b_e[j]:
                i += 1
            else:
                j += 1
        return IntervalSet.from_arrays(out_s, out_e)

    def subtract(self, other: "IntervalSet") -> "IntervalSet":
        o_s, o_e = other.starts, other.ends
        out_s, out_e = array("Q"), array("Q")
        j = 0
        m = len(o_s)
        for s, e in self:
            cur = s
            while j < m and o_e[j] <= cur:
                j += 1
            k = j
            while k < m and o_s[k] < e:
                if o_s[k] > cur:
                    out_s.append(cur)
                    out_e.append(o_s[k])
                cur = max(cur, o_e[k])
                if cur >= e:
                    break
                k += 1
            if cur < e:
                out_s.append(cur)
                out_e.append(e)
        return IntervalSet.from_arrays(out_s, out_e)

    def union(self, other: "IntervalSet") -> "IntervalSet":
        a_s, a_e, b_s, b_e = self.starts, self.ends, other.starts, other.ends
        out_s, out_e = array("Q"), array("Q")
        i = j = 0
        n, m = len(a_s), len(b_s)
        while i < n or j < m:
            if j >= m or (i < n and a_s[i] <= b_s[j]):
                s, e = a_s[i], a_e[i]
                i += 1
            else:
                s, e = b_s[j], b_e[j]
                j += 1
            if out_e and s <= out_e[-1]:
                if e > out_e[-1]:
                    out_e[-1] = e
            else:
                out_s.append(s)
                out_e.append(e)
        return IntervalSet.from_arrays(out_s, out_e)
