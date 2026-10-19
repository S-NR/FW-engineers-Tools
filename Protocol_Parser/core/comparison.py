"""TX/RX comparison: byte-level and field-level differences between two packets."""
from __future__ import annotations

from dataclasses import dataclass

from core.decoder import DecodeResult

__all__ = ["ByteDiff", "FieldComparison", "byte_diff", "compare_fields", "text_fields"]


@dataclass(frozen=True)
class ByteDiff:
    offset: int
    a: int | None
    b: int | None

    @property
    def same(self) -> bool:
        return self.a == self.b


@dataclass(frozen=True)
class FieldComparison:
    name: str
    a_display: str | None  # None = field absent in A
    b_display: str | None

    @property
    def same(self) -> bool:
        return self.a_display == self.b_display


def byte_diff(a: bytes, b: bytes) -> list[ByteDiff]:
    """Positional comparison; offsets past the shorter packet have ``None`` on one side."""
    n = max(len(a), len(b))
    return [ByteDiff(i, a[i] if i < len(a) else None, b[i] if i < len(b) else None) for i in range(n)]


def compare_fields(a: DecodeResult, b: DecodeResult) -> list[FieldComparison]:
    """Field-by-field comparison by name (defined fields only), in protocol order."""
    names: list[str] = []
    for res in (a, b):
        for f in res.fields:
            if f.defined and f.name not in names:
                names.append(f.name)
    out = []
    for name in names:
        fa, fb = a.field(name), b.field(name)
        out.append(FieldComparison(name, fa.display if fa else None, fb.display if fb else None))
    return out


def text_fields(result: DecodeResult) -> list[tuple[str, str]]:
    """(name, text) of decoded ASCII/UTF-8 fields - typically the command/response."""
    return [(f.name, f.value) for f in result.fields
            if f.defined and f.field_type in ("ascii", "utf8") and isinstance(f.value, str)]
