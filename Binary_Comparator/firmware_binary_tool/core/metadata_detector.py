"""Heuristic detection of firmware metadata strings.

Printable ASCII strings are extracted from the image data and matched
against common patterns (version numbers, __DATE__/__TIME__ strings, git
hashes, product/board names).

DETECTION IS HEURISTIC AND NOT GUARANTEED: strings may be missed (compressed,
encrypted, UTF-16 or binary-encoded metadata) and unrelated strings may be
reported (false positives). Results are always labelled with a confidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

DISCLAIMER = ("Metadata detection is heuristic: it scans printable ASCII strings for common patterns. "
              "Results are not guaranteed - values may be missed or misidentified.")

CATEGORIES = ["Firmware version", "Build date", "Build time", "Git commit", "Product name", "Board name"]

_STRING_RE = re.compile(rb"[\x20-\x7E]{4,200}")
MAX_STRINGS = 200_000
MAX_ITEMS_PER_CATEGORY = 25

_MONTHS = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"

# (category, compiled regex, group index, confidence)
_PATTERNS: list[tuple[str, re.Pattern, int, str]] = [
    ("Firmware version", re.compile(
        r"(?i)(?<![A-Za-z])(?:fw|firmware|sw|software|app|version|ver|rev)[\s._:=-]*v?(\d+\.\d+(?:\.\d+){0,2}(?:[-+][0-9A-Za-z.]+)?)"),
     1, "high"),
    ("Firmware version", re.compile(r"(?<![\w.])v(\d+\.\d+(?:\.\d+){0,2}(?:-[0-9A-Za-z.]+)?)\b"), 1, "medium"),
    ("Firmware version", re.compile(r"(?<![\w.])(\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?)(?![\w.])"), 1, "low"),
    ("Build date", re.compile(rf"\b({_MONTHS} [ \d]\d \d{{4}})\b"), 1, "high"),  # __DATE__
    ("Build date", re.compile(r"\b(20\d\d-[01]\d-[0-3]\d)\b"), 1, "medium"),
    ("Build date", re.compile(r"\b(20\d\d/[01]\d/[0-3]\d)\b"), 1, "medium"),
    ("Build time", re.compile(r"(?<![\d:])([0-2]\d:[0-5]\d:[0-5]\d)(?![\d:])"), 1, "medium"),
    ("Git commit", re.compile(r"(?i)\b(?:git|commit|sha|hash|rev(?:ision)?)[\s:=_#-]*([0-9a-f]{7,40})\b"), 1, "high"),
    ("Git commit", re.compile(r"-g([0-9a-f]{7,40})\b"), 1, "medium"),  # git describe
    ("Git commit", re.compile(r"(?<![0-9A-Za-z])([0-9a-f]{40})(?![0-9A-Za-z])"), 1, "low"),
    ("Product name", re.compile(r"(?i)\b(?:product|model|device)(?:[ _-]?name)?\s*[:=]\s*([A-Za-z0-9][\w .+/-]{1,40})"), 1, "high"),
    ("Board name", re.compile(r"(?i)\b(?:board|hw|hardware|pcb)(?:[ _-]?(?:name|rev|id))?\s*[:=]\s*([A-Za-z0-9][\w .+/-]{1,40})"), 1, "high"),
]
_CONF_ORDER = {"high": 0, "medium": 1, "low": 2}


@dataclass
class MetadataItem:
    category: str
    value: str
    address: int
    confidence: str
    context: str  # the full string the value was found in


@dataclass
class MetadataDiff:
    category: str
    values_a: list[str]
    values_b: list[str]

    @property
    def status(self) -> str:
        if not self.values_a and not self.values_b:
            return "NOT FOUND"
        if not self.values_a:
            return "ONLY IN B"
        if not self.values_b:
            return "ONLY IN A"
        return "SAME" if set(self.values_a) == set(self.values_b) else "CHANGED"


def extract_strings(image, min_len: int = 4, limit: int = MAX_STRINGS):
    """Yield (address, text) for printable ASCII runs in the image."""
    pattern = _STRING_RE if min_len == 4 else re.compile(rb"[\x20-\x7E]{%d,200}" % min_len)
    count = 0
    for seg in image.segments:
        for m in pattern.finditer(seg.data):
            yield seg.start + m.start(), m.group().decode("ascii")
            count += 1
            if count >= limit:
                return


def detect_metadata(image) -> list[MetadataItem]:
    items: dict[tuple[str, str], MetadataItem] = {}
    per_cat: dict[str, int] = {}
    for addr, text in extract_strings(image):
        for category, rx, group, conf in _PATTERNS:
            for m in rx.finditer(text):
                value = m.group(group).strip()
                key = (category, value)
                existing = items.get(key)
                if existing is not None:
                    if _CONF_ORDER[conf] < _CONF_ORDER[existing.confidence]:
                        existing.confidence = conf
                    continue
                if per_cat.get(category, 0) >= MAX_ITEMS_PER_CATEGORY:
                    continue
                per_cat[category] = per_cat.get(category, 0) + 1
                items[key] = MetadataItem(category, value, addr + m.start(group), conf, text.strip())
    # Drop low-confidence versions that also appear with a higher confidence elsewhere: handled by key dedupe.
    return sorted(items.values(), key=lambda i: (CATEGORIES.index(i.category), _CONF_ORDER[i.confidence], i.address))


def best_values(items: list[MetadataItem], category: str) -> list[str]:
    """Values of a category, keeping only the best confidence level found."""
    cat = [i for i in items if i.category == category]
    if not cat:
        return []
    best = min(_CONF_ORDER[i.confidence] for i in cat)
    return list(dict.fromkeys(i.value for i in cat if _CONF_ORDER[i.confidence] == best))


def compare_metadata(items_a: list[MetadataItem], items_b: list[MetadataItem]) -> list[MetadataDiff]:
    return [MetadataDiff(c, best_values(items_a, c), best_values(items_b, c)) for c in CATEGORIES]
