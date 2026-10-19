"""Multi-packet difference analysis for reverse engineering.

Facts (which bytes are constant / changing, arithmetic patterns, verified CRC
placements) are computed deterministically. Meanings are only ever offered as
"Possible interpretation" and never asserted.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.checksum import find_matching_checksum
from core.crc import CRCMatch, find_matching_crc

__all__ = ["OffsetAnalysis", "RegionAnalysis", "DiffReport", "analyze_packets", "classify_sequence"]

POSSIBLE = "Possible interpretation: "


@dataclass
class OffsetAnalysis:
    offset: int
    values: list[int | None]
    pattern: str
    interpretation: str = ""

    @property
    def varying(self) -> bool:
        present = [v for v in self.values if v is not None]
        return len(set(present)) > 1 or len(present) != len(self.values)


@dataclass
class RegionAnalysis:
    start: int
    end: int  # inclusive
    pattern: str
    interpretation: str = ""

    @property
    def label(self) -> str:
        return str(self.start) if self.start == self.end else f"{self.start}-{self.end}"


@dataclass
class DiffReport:
    packet_count: int
    lengths: list[int]
    offsets: list[OffsetAnalysis] = field(default_factory=list)
    regions: list[RegionAnalysis] = field(default_factory=list)
    crc_matches: list[CRCMatch] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def varying_offsets(self) -> list[OffsetAnalysis]:
        return [o for o in self.offsets if o.varying]

    def to_text(self, field_names: dict[int, str] | None = None) -> str:
        field_names = field_names or {}
        lines = [f"Packets analysed: {self.packet_count}", f"Lengths: {', '.join(map(str, self.lengths))}", ""]
        lines.extend(self.notes)
        if self.notes:
            lines.append("")
        for o in self.varying_offsets:
            name = f" ({field_names[o.offset]})" if o.offset in field_names else ""
            lines.append(f"Offset {o.offset}{name}")
            for i, v in enumerate(o.values, 1):
                lines.append(f"  Packet {i}: {'--' if v is None else f'{v:02X}'}")
            lines.append(f"  Pattern: {o.pattern}")
            if o.interpretation:
                lines.append(f"  {o.interpretation}")
            lines.append("")
        for r in self.regions:
            lines.append(f"Bytes {r.label}: {r.pattern}")
            if r.interpretation:
                lines.append(f"  {r.interpretation}")
        for m in self.crc_matches:
            lines.append(f"Verified in all packets: {m.describe()}")
        return "\n".join(lines).rstrip()


def classify_sequence(values: list[int], modulus: int = 256) -> str:
    """Classify a numeric sequence (no None values)."""
    if len(values) < 2:
        return "Single sample"
    if len(set(values)) == 1:
        return "Constant"
    diffs = [(b - a) % modulus for a, b in zip(values, values[1:])]
    if len(set(diffs)) == 1:
        step = diffs[0]
        if step == 1:
            return "Incrementing (+1)"
        if step == modulus - 1:
            return "Decrementing (-1)"
        if step > modulus // 2:
            return f"Decrementing (step -{modulus - step})"
        return f"Incrementing (step +{step})"
    if len(values) >= 3 and len(set(values)) == 2 and all(a != b for a, b in zip(values, values[1:])):
        return "Alternating between two values"
    # Wrap-around is only assumed for constant steps (counters); trends use plain differences.
    raw_diffs = [b - a for a, b in zip(values, values[1:])]
    if len(values) >= 3 and all(d > 0 for d in raw_diffs):
        return "Increasing (irregular steps)"
    if len(values) >= 3 and all(d < 0 for d in raw_diffs):
        return "Decreasing (irregular steps)"
    return "Varying"


def _interpret(pattern: str, near_end: bool) -> str:
    if near_end and pattern in ("Varying",):
        return POSSIBLE + "checksum / CRC (changes whenever the content changes)"
    if pattern.startswith(("Incrementing", "Decrementing")):
        return POSSIBLE + "counter / sequence number / measurement value"
    if pattern.startswith("Alternating"):
        return POSSIBLE + "toggle flag / alternating state / bit field"
    if pattern.startswith(("Increasing", "Decreasing")):
        return POSSIBLE + "measurement value / timestamp"
    if pattern == "Varying":
        return POSSIBLE + "data / measurement / payload"
    return ""


def analyze_packets(packets: list[bytes], crc_search: bool = True) -> DiffReport:
    packets = [bytes(p) for p in packets if p]
    report = DiffReport(len(packets), [len(p) for p in packets])
    if len(packets) < 2:
        report.notes.append("At least two packets are needed for a difference analysis.")
        return report

    if len(set(report.lengths)) > 1:
        report.notes.append(
            f"Packet lengths differ ({min(report.lengths)}-{max(report.lengths)} bytes); offsets are compared "
            f"positionally, so fields after a variable-length section may not line up."
        )

    max_len = max(report.lengths)
    min_len = min(report.lengths)
    # Checksum candidates: the last 2 bytes, or the 2 before a constant end byte (ETX).
    end_constant = len({p[-1] for p in packets}) == 1
    tail_start = min_len - (3 if end_constant else 2)

    for off in range(max_len):
        values = [p[off] if off < len(p) else None for p in packets]
        present = [v for v in values if v is not None]
        if len(present) != len(values):
            pattern, interp = "Absent in some packets", ""
        else:
            pattern = classify_sequence(present)
            interp = _interpret(pattern, near_end=off >= tail_start) if pattern != "Constant" else ""
        report.offsets.append(OffsetAnalysis(off, values, pattern, interp))

    _analyze_regions(report, packets, min_len)

    if crc_search:
        report.crc_matches = find_matching_crc(packets) + find_matching_checksum(packets)
        if report.crc_matches and len(packets) < 4:
            report.notes.append("CRC/checksum matches found with few packets - verify with more samples.")

    constant = sum(1 for o in report.offsets if not o.varying)
    report.notes.insert(0, f"{constant} of {max_len} offsets are constant across all packets; "
                           f"{max_len - constant} change.")
    return report


def _analyze_regions(report: DiffReport, packets: list[bytes], min_len: int) -> None:
    """Group adjacent varying offsets and test them as 16/32-bit integers."""
    varying = [o.offset for o in report.offsets if o.varying and o.offset < min_len]
    groups: list[list[int]] = []
    for off in varying:
        if groups and off == groups[-1][-1] + 1:
            groups[-1].append(off)
        else:
            groups.append([off])

    for g in groups:
        start, end = g[0], g[-1]
        # A changing byte often has a constant high/low byte neighbour: try 2-byte windows around it.
        candidates = []
        if len(g) in (2, 4):
            candidates.append((start, len(g)))
        elif len(g) == 1:
            if start + 1 < min_len:
                candidates.append((start, 2))
            if start >= 1:
                candidates.append((start - 1, 2))
        for cstart, width in candidates:
            for order in ("little", "big"):
                vals = [int.from_bytes(p[cstart:cstart + width], order) for p in packets]
                pattern = classify_sequence(vals, modulus=1 << (8 * width))
                if pattern.startswith(("Incrementing", "Decrementing")) and len(g) > 1:
                    report.regions.append(RegionAnalysis(
                        cstart, cstart + width - 1,
                        f"{8 * width}-bit {order}-endian value: {pattern} "
                        f"({', '.join(str(v) for v in vals)})",
                        POSSIBLE + "multi-byte counter / sequence number / measurement value"))
                    break
            else:
                continue
            break

        if len(g) > 1 and not any(r.start <= start <= r.end for r in report.regions):
            report.regions.append(RegionAnalysis(start, end, f"{len(g)} adjacent changing bytes",
                                                 POSSIBLE + "multi-byte field or payload section"))
