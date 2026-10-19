"""GUI-independent data model for the memory-map visualisation.

The map uses a *compressed* address axis: only addresses that hold data in
at least one image are drawn, so sparse images (e.g. HEX files with data at
0x08000000 and 0x1FFF0000) still produce a useful picture. Gaps are reported
separately so the visualisation can mark them.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field

from .intervals import IntervalSet

CELL_NODATA, CELL_SAME, CELL_EXPECTED, CELL_CHANGED = "nodata", "same", "expected", "changed"


@dataclass
class MapCell:
    index: int
    pieces: list[tuple[int, int]] = field(default_factory=list)
    data_bytes: int = 0
    changed: int = 0
    expected: int = 0

    @property
    def start(self) -> int:
        return self.pieces[0][0]

    @property
    def end(self) -> int:
        """Exclusive end address of the last piece."""
        return self.pieces[-1][1]

    @property
    def unexpected(self) -> int:
        return self.changed - self.expected

    @property
    def status(self) -> str:
        if self.data_bytes == 0:
            return CELL_NODATA
        if self.unexpected > 0:
            return CELL_CHANGED
        if self.expected > 0:
            return CELL_EXPECTED
        return CELL_SAME

    @property
    def changed_fraction(self) -> float:
        return self.changed / self.data_bytes if self.data_bytes else 0.0


def rank(union: IntervalSet, addr: int) -> int:
    """Number of data addresses strictly below *addr* (position on the compressed axis)."""
    i = bisect_right(union.starts, addr) - 1
    if i < 0:
        return 0
    s, e = union.starts[i], union.ends[i]
    return union._prefix[i] + (min(addr, e) - s)


def build_cells(union: IntervalSet, diff: IntervalSet, expected_diff: IntervalSet, ncells: int) -> tuple[list[MapCell], int]:
    """Split the compressed axis into at most *ncells* cells. Returns (cells, bytes_per_cell)."""
    total = union.total
    if total == 0 or ncells <= 0:
        return [], 0
    per = max(1, -(-total // ncells))
    cells: list[MapCell] = []
    cur = MapCell(0)
    room = per
    for s, e in union:
        pos = s
        while pos < e:
            take = min(room, e - pos)
            cur.pieces.append((pos, pos + take))
            cur.data_bytes += take
            cur.changed += diff.overlap(pos, pos + take)
            cur.expected += expected_diff.overlap(pos, pos + take)
            pos += take
            room -= take
            if room == 0:
                cells.append(cur)
                cur = MapCell(len(cells))
                room = per
    if cur.pieces:
        cells.append(cur)
    return cells, per


def gap_positions(union: IntervalSet) -> list[tuple[int, int, int]]:
    """(compressed position, gap start, gap end) for every hole between data intervals."""
    out = []
    for i in range(1, len(union)):
        out.append((union._prefix[i], union.ends[i - 1], union.starts[i]))
    return out
