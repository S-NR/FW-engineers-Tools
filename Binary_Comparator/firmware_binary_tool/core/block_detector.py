"""Grouping of differing bytes into contiguous changed blocks."""

from __future__ import annotations

from dataclasses import dataclass, field

from .intervals import IntervalSet

EXPECTED, UNEXPECTED, MIXED = "EXPECTED", "UNEXPECTED", "MIXED"


@dataclass(slots=True)
class ChangedBlock:
    index: int
    start: int
    end: int  # exclusive
    changed_bytes: int
    only_a_bytes: int = 0
    only_b_bytes: int = 0
    expected_bytes: int = 0
    regions: list[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        return self.end - self.start

    @property
    def last(self) -> int:
        """Last address of the block (inclusive)."""
        return self.end - 1

    @property
    def unexpected_bytes(self) -> int:
        return self.changed_bytes - self.expected_bytes

    @property
    def classification(self) -> str:
        if self.unexpected_bytes == 0:
            return EXPECTED
        if self.expected_bytes == 0:
            return UNEXPECTED
        return MIXED

    @property
    def region_label(self) -> str:
        return ", ".join(self.regions) if self.regions else "(outside regions)"

    @property
    def kind(self) -> str:
        """What kind of difference the block contains."""
        parts = []
        content = self.changed_bytes - self.only_a_bytes - self.only_b_bytes
        if content:
            parts.append("modified")
        if self.only_a_bytes:
            parts.append("only in A")
        if self.only_b_bytes:
            parts.append("only in B")
        return ", ".join(parts)


def group_runs(diff: IntervalSet, merge_gap: int = 0) -> list[tuple[int, int]]:
    """Group differing runs into blocks.

    With ``merge_gap == 0`` every contiguous run of changed bytes is a block.
    A positive ``merge_gap`` joins runs separated by at most that many
    unchanged bytes (the block then reports both its size and the number of
    bytes that actually changed).
    """
    if merge_gap < 0:
        raise ValueError("merge_gap must be >= 0")
    groups: list[list[int]] = []
    for s, e in diff:
        if groups and s - groups[-1][1] <= merge_gap:
            groups[-1][1] = e
        else:
            groups.append([s, e])
    return [(s, e) for s, e in groups]


def detect_blocks(diff: IntervalSet, merge_gap: int = 0, only_a: IntervalSet | None = None,
                  only_b: IntervalSet | None = None, expected_diff: IntervalSet | None = None,
                  regions: list | None = None) -> list[ChangedBlock]:
    """Build :class:`ChangedBlock` objects with per-block statistics.

    *regions* is a list of objects with ``name``, ``start`` and ``stop``
    (exclusive) attributes.
    """
    regions = sorted(regions or [], key=lambda r: r.start)
    blocks: list[ChangedBlock] = []
    for idx, (s, e) in enumerate(group_runs(diff, merge_gap), 1):
        blk = ChangedBlock(idx, s, e, diff.overlap(s, e))
        if only_a is not None:
            blk.only_a_bytes = only_a.overlap(s, e)
        if only_b is not None:
            blk.only_b_bytes = only_b.overlap(s, e)
        if expected_diff is not None:
            blk.expected_bytes = expected_diff.overlap(s, e)
        for r in regions:
            if r.start >= e:
                break
            if r.stop > s and diff.overlap(max(s, r.start), min(e, r.stop)):
                blk.regions.append(r.name)
        blocks.append(blk)
    return blocks


SORT_KEYS = {
    "address": lambda b: b.start,
    "size": lambda b: (-b.size, b.start),
    "changed": lambda b: (-b.changed_bytes, b.start),
    "region": lambda b: (b.region_label.lower(), b.start),
}


def sort_blocks(blocks: list[ChangedBlock], key: str = "address") -> list[ChangedBlock]:
    try:
        return sorted(blocks, key=SORT_KEYS[key])
    except KeyError:
        raise ValueError(f"unknown sort key '{key}' (use one of {', '.join(SORT_KEYS)})") from None
