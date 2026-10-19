"""Region / expected-area analysis on top of a raw :class:`DiffResult`.

The expensive byte comparison is done once; this analysis is cheap and is
re-run whenever the region or expected-area configuration changes.

Expected areas are *never* assumed: a difference is EXPECTED only if it lies
inside an area the user explicitly configured. Everything else is UNEXPECTED.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .binary_comparator import DiffResult
from .block_detector import ChangedBlock, detect_blocks
from .intervals import IntervalSet
from .region_manager import MemoryRegion, ProjectConfig

# Region statuses
ST_SAME = "SAME"
ST_CHANGED = "CHANGED"
ST_EXPECTED = "EXPECTED CHANGE"
ST_NOT_PRESENT = "NOT PRESENT"

# Overall verdicts
V_IDENTICAL = "IDENTICAL"
V_EXPECTED_ONLY = "EXPECTED CHANGES ONLY"
V_UNEXPECTED = "UNEXPECTED CHANGES"


@dataclass
class RegionResult:
    region: MemoryRegion
    covered_a: int
    covered_b: int
    changed_bytes: int
    expected_bytes: int
    status: str

    @property
    def size(self) -> int:
        return self.region.size

    @property
    def unexpected_bytes(self) -> int:
        return self.changed_bytes - self.expected_bytes

    @property
    def changed_percent(self) -> float:
        return self.changed_bytes * 100.0 / self.size if self.size else 0.0

    @property
    def coverage_note(self) -> str:
        size = self.size
        if self.covered_a == size and self.covered_b == size:
            return "full"
        return (f"A {self.covered_a * 100.0 / size:.1f}% / "
                f"B {self.covered_b * 100.0 / size:.1f}%")


@dataclass
class ExpectedAreaResult:
    area: MemoryRegion
    changed_bytes: int
    present: bool

    @property
    def status(self) -> str:
        if not self.present:
            return "NOT PRESENT"
        return "CHANGED (EXPECTED)" if self.changed_bytes else "UNCHANGED"


@dataclass
class Analysis:
    diff: DiffResult
    config: ProjectConfig
    blocks: list[ChangedBlock]
    region_results: list[RegionResult]
    expected_results: list[ExpectedAreaResult]
    expected_bytes: int
    outside_changed: int  # changed bytes outside every configured region
    outside_expected: int
    config_warnings: list[str] = field(default_factory=list)
    expected_set: IntervalSet = field(default_factory=IntervalSet)
    expected_diff: IntervalSet = field(default_factory=IntervalSet)

    @property
    def unexpected_bytes(self) -> int:
        return self.diff.diff_bytes - self.expected_bytes

    @property
    def verdict(self) -> str:
        if self.diff.identical:
            return V_IDENTICAL
        return V_EXPECTED_ONLY if self.unexpected_bytes == 0 else V_UNEXPECTED

    @property
    def largest_block(self) -> ChangedBlock | None:
        return max(self.blocks, key=lambda b: (b.size, -b.start), default=None)

    @property
    def unexpected_blocks(self) -> list[ChangedBlock]:
        return [b for b in self.blocks if b.unexpected_bytes > 0]

    @property
    def expected_blocks(self) -> list[ChangedBlock]:
        return [b for b in self.blocks if b.expected_bytes > 0]

    def is_expected(self, addr: int) -> bool:
        return self.expected_set.contains(addr)

    def regions_at(self, addr: int) -> list[str]:
        return [r.name for r in self.config.regions if r.contains(addr)]


def analyze(diff: DiffResult, config: ProjectConfig) -> Analysis:
    warnings = config.check()
    expected_set = IntervalSet((r.start, r.stop) for r in config.expected)
    expected_diff = diff.diff.intersect(expected_set)

    blocks = detect_blocks(diff.diff, config.merge_gap, diff.only_a, diff.only_b, expected_diff, config.regions)

    region_results = []
    for r in config.regions:
        s, e = r.start, r.stop
        cov_a, cov_b = diff.coverage_a.overlap(s, e), diff.coverage_b.overlap(s, e)
        changed = diff.diff.overlap(s, e)
        exp = expected_diff.overlap(s, e)
        if cov_a == 0 and cov_b == 0:
            status = ST_NOT_PRESENT
        elif changed == 0:
            status = ST_SAME
        elif changed == exp:
            status = ST_EXPECTED
        else:
            status = ST_CHANGED
        region_results.append(RegionResult(r, cov_a, cov_b, changed, exp, status))

    outside_changed = outside_expected = 0
    if config.regions:
        region_union = IntervalSet((r.start, r.stop) for r in config.regions)
        outside_changed = diff.diff.total - diff.diff.intersect(region_union).total
        outside_expected = expected_diff.total - expected_diff.intersect(region_union).total

    expected_results = [
        ExpectedAreaResult(a, diff.diff.overlap(a.start, a.stop), diff.union.overlap(a.start, a.stop) > 0)
        for a in config.expected
    ]
    return Analysis(diff, config, blocks, region_results, expected_results, expected_diff.total,
                    outside_changed, outside_expected, warnings, expected_set, expected_diff)
