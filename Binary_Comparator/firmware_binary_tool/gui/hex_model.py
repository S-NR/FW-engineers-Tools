"""Row model for the virtual hex comparison viewer (no Tk dependency).

The viewer never creates widgets per byte. It asks this model which address
range belongs to row *n* of the current (filtered) view and renders only the
visible rows, so multi-megabyte images scroll instantly.
"""

from __future__ import annotations

from array import array
from bisect import bisect_right

from ..core.intervals import IntervalSet, merge_intervals

FILTER_ALL = "Show all"
FILTER_DIFF = "Only differences"
FILTER_SAME = "Only unchanged"
FILTER_BLOCKS = "Blocks larger than X bytes"
FILTERS = [FILTER_ALL, FILTER_DIFF, FILTER_SAME, FILTER_BLOCKS]


class HexRowModel:
    def __init__(self, intervals: IntervalSet, bytes_per_row: int):
        self.bpr = bytes_per_row
        aligned = merge_intervals(
            (s - s % bytes_per_row, e + (-e) % bytes_per_row) for s, e in intervals)
        self.starts = array("Q", (s for s, _ in aligned))
        self.cum = array("Q", [0])  # rows before interval i
        for s, e in aligned:
            self.cum.append(self.cum[-1] + (e - s) // bytes_per_row)
        self.visible = intervals  # exact filtered addresses (rows may include neighbours)

    @property
    def total_rows(self) -> int:
        return self.cum[-1]

    def row_to_addr(self, row: int) -> int | None:
        if row < 0 or row >= self.total_rows:
            return None
        i = bisect_right(self.cum, row) - 1
        return self.starts[i] + (row - self.cum[i]) * self.bpr

    def addr_to_row(self, addr: int) -> int | None:
        """Row containing *addr*, or the next row after it, or the last row."""
        if self.total_rows == 0:
            return None
        i = bisect_right(self.starts, addr) - 1
        if i >= 0:
            span_rows = self.cum[i + 1] - self.cum[i]
            rel = (addr - self.starts[i]) // self.bpr
            if rel < span_rows:
                return self.cum[i] + rel
        if i + 1 < len(self.starts):
            return self.cum[i + 1]
        return self.total_rows - 1

    def contains_addr(self, addr: int) -> bool:
        return self.visible.contains(addr)


def build_view_intervals(diff, analysis, mode: str, region=None, min_block: int = 0) -> IntervalSet:
    """Address set shown for a filter mode, optionally restricted to a region."""
    if mode == FILTER_DIFF:
        base = diff.diff
    elif mode == FILTER_SAME:
        base = diff.union.subtract(diff.diff)
    elif mode == FILTER_BLOCKS:
        base = IntervalSet((b.start, b.end) for b in analysis.blocks if b.size > min_block)
    else:
        base = diff.union
    if region is not None:
        base = base.intersect(IntervalSet([(region.start, region.stop)]))
    return base
