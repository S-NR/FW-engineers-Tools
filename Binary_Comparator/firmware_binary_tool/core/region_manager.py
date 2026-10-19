"""Named memory regions, expected-difference areas and project configuration.

Region ranges are entered *inclusive* (``0x08000000 - 0x08007FFF``) as engineers
write them in linker scripts and datasheets; internally ``stop`` is exclusive.

Configuration file (JSON)::

    {
      "name": "STM32F4 product",
      "base_address": "0x08000000",
      "merge_gap": 0,
      "gap_fill": null,            (optional, e.g. "0xFF": compare one-sided bytes with erased flash)
      "regions":  [{"name": "Bootloader", "start": "0x08000000", "end": "0x08007FFF"}],
      "expected": [{"name": "CRC", "start": "0x0807FFFC", "end": "0x0807FFFF",
                    "description": "image CRC"}]
    }

``"size"`` may be given instead of ``"end"``. ``"ignore"`` is accepted as an
alias of ``"expected"``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..errors import ConfigError
from .formatting import parse_int

CONFIG_VERSION = 1


@dataclass
class MemoryRegion:
    name: str
    start: int
    end: int  # inclusive
    description: str = ""

    @property
    def stop(self) -> int:
        """Exclusive end address."""
        return self.end + 1

    @property
    def size(self) -> int:
        return self.end - self.start + 1

    def contains(self, addr: int) -> bool:
        return self.start <= addr <= self.end

    def to_dict(self) -> dict:
        d = {"name": self.name, "start": f"0x{self.start:08X}", "end": f"0x{self.end:08X}"}
        if self.description:
            d["description"] = self.description
        return d

    @classmethod
    def from_dict(cls, d: dict, where: str = "region") -> "MemoryRegion":
        if not isinstance(d, dict):
            raise ConfigError(f"{where}: expected an object, got {type(d).__name__}")
        name = str(d.get("name", "")).strip()
        if not name:
            raise ConfigError(f"{where}: missing 'name'")
        try:
            start = parse_int(d["start"])
        except KeyError:
            raise ConfigError(f"{where} '{name}': missing 'start'") from None
        except ValueError as exc:
            raise ConfigError(f"{where} '{name}': start: {exc}") from None
        try:
            if "end" in d:
                end = parse_int(d["end"])
            elif "size" in d:
                size = parse_int(d["size"])
                if size == 0:
                    raise ConfigError(f"{where} '{name}': size must be > 0")
                end = start + size - 1
            else:
                raise ConfigError(f"{where} '{name}': needs 'end' (inclusive) or 'size'")
        except ValueError as exc:
            raise ConfigError(f"{where} '{name}': end/size: {exc}") from None
        if end < start:
            raise ConfigError(f"{where} '{name}': end 0x{end:X} is before start 0x{start:X}")
        return cls(name, start, end, str(d.get("description", "")))


def parse_range_text(start_text: str, end_text: str) -> tuple[int, int]:
    """Parse GUI range input. *end_text* may be '+size' instead of an end address."""
    start = parse_int(start_text)
    end_text = end_text.strip()
    if end_text.startswith("+"):
        size = parse_int(end_text[1:])
        if size == 0:
            raise ValueError("size must be greater than zero")
        end = start + size - 1
    else:
        end = parse_int(end_text)
    if end < start:
        raise ValueError(f"end 0x{end:X} is before start 0x{start:X}")
    return start, end


def validate_regions(regions: list[MemoryRegion], kind: str = "Region") -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for a list of regions."""
    errors: list[str] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for r in regions:
        if not r.name.strip():
            errors.append(f"{kind} with empty name at 0x{r.start:08X}")
        if r.name.lower() in seen:
            errors.append(f"Duplicate {kind.lower()} name '{r.name}'")
        seen.add(r.name.lower())
        if r.end < r.start:
            errors.append(f"{kind} '{r.name}': end 0x{r.end:X} is before start 0x{r.start:X}")
        if r.start < 0 or r.end >= 1 << 64:
            errors.append(f"{kind} '{r.name}': address outside 64-bit range")
    ordered = sorted(regions, key=lambda r: r.start)
    for a, b in zip(ordered, ordered[1:]):
        if b.start <= a.end:
            warnings.append(
                f"{kind}s '{a.name}' (0x{a.start:08X}-0x{a.end:08X}) and '{b.name}' "
                f"(0x{b.start:08X}-0x{b.end:08X}) overlap; changed bytes in the overlap are counted in both."
            )
    return errors, warnings


@dataclass
class ProjectConfig:
    name: str = ""
    regions: list[MemoryRegion] = field(default_factory=list)
    expected: list[MemoryRegion] = field(default_factory=list)
    base_address: int | None = None  # default base address for raw .bin files
    merge_gap: int = 0
    notes: str = ""
    gap_fill: int | None = None  # compare one-sided bytes against this value (None = strict)

    def validate(self) -> tuple[list[str], list[str]]:
        e1, w1 = validate_regions(self.regions, "Region")
        e2, w2 = validate_regions(self.expected, "Expected area")
        errors = e1 + e2
        if self.merge_gap < 0:
            errors.append("merge_gap must be >= 0")
        if self.gap_fill is not None and not 0 <= self.gap_fill <= 0xFF:
            errors.append("gap_fill must be a byte value 0x00-0xFF")
        return errors, w1 + w2

    def check(self) -> list[str]:
        """Raise ConfigError on errors, return warnings."""
        errors, warnings = self.validate()
        if errors:
            raise ConfigError("Invalid configuration:\n  " + "\n  ".join(errors))
        return warnings

    def to_dict(self) -> dict:
        d: dict = {"config_version": CONFIG_VERSION, "name": self.name}
        if self.base_address is not None:
            d["base_address"] = f"0x{self.base_address:08X}"
        d["merge_gap"] = self.merge_gap
        if self.gap_fill is not None:
            d["gap_fill"] = f"0x{self.gap_fill:02X}"
        d["regions"] = [r.to_dict() for r in self.regions]
        d["expected"] = [r.to_dict() for r in self.expected]
        if self.notes:
            d["notes"] = self.notes
        return d

    @classmethod
    def from_dict(cls, d) -> "ProjectConfig":
        if isinstance(d, list):  # bare list of regions
            d = {"regions": d}
        if not isinstance(d, dict):
            raise ConfigError("Configuration must be a JSON object or a list of regions")
        cfg = cls(name=str(d.get("name", "")), notes=str(d.get("notes", "")))
        if d.get("base_address") is not None:
            try:
                cfg.base_address = parse_int(d["base_address"])
            except ValueError as exc:
                raise ConfigError(f"base_address: {exc}") from None
        try:
            cfg.merge_gap = parse_int(d.get("merge_gap", 0))
        except ValueError as exc:
            raise ConfigError(f"merge_gap: {exc}") from None
        if d.get("gap_fill") is not None:
            try:
                cfg.gap_fill = parse_int(d["gap_fill"])
            except ValueError as exc:
                raise ConfigError(f"gap_fill: {exc}") from None
        regions = d.get("regions", [])
        expected = d.get("expected", d.get("ignore", []))
        if not isinstance(regions, list) or not isinstance(expected, list):
            raise ConfigError("'regions' and 'expected' must be lists")
        cfg.regions = [MemoryRegion.from_dict(r, f"regions[{i}]") for i, r in enumerate(regions)]
        cfg.expected = [MemoryRegion.from_dict(r, f"expected[{i}]") for i, r in enumerate(expected)]
        return cfg

    @classmethod
    def load(cls, path: str) -> "ProjectConfig":
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except OSError as exc:
            raise ConfigError(f"Cannot read configuration '{path}': {exc}") from None
        except json.JSONDecodeError as exc:
            raise ConfigError(f"Configuration '{path}' is not valid JSON: {exc}") from None
        return cls.from_dict(data)

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)
            f.write("\n")

    def merged_with_expected(self, other: "ProjectConfig") -> "ProjectConfig":
        """Return a copy with *other*'s expected areas appended (CLI --ignore)."""
        return ProjectConfig(self.name, list(self.regions), list(self.expected) + list(other.expected),
                             self.base_address, self.merge_gap, self.notes, self.gap_fill)
