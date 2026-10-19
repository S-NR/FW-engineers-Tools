"""Field layout resolution: where does each field sit in a concrete packet?

This step handles dynamic offsets/lengths (``auto``, ``last``, negative
offsets, ``length_from``, ``remaining``). It reads integer values only where
another field's length depends on them; full value decoding is done by
:mod:`core.decoder`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.field_decoder import decode_int, decode_uint
from core.protocol import FieldDefinition, ProtocolDefinition

__all__ = ["FieldSlice", "Layout", "resolve_layout"]


@dataclass
class FieldSlice:
    field: FieldDefinition
    offset: int
    length: int  # bytes actually available in the packet
    expected_length: int  # bytes the definition asked for
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def end(self) -> int:
        """Exclusive end offset."""
        return self.offset + self.length

    @property
    def complete(self) -> bool:
        return self.length == self.expected_length


@dataclass
class Layout:
    slices: list[FieldSlice]
    packet_length: int
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def by_name(self, name: str) -> FieldSlice | None:
        for s in self.slices:
            if s.field.name == name:
                return s
        return None


def _tail_size(fields: list[FieldDefinition]) -> int:
    return sum(f.fixed_size() or 0 for f in fields)


def resolve_layout(protocol: ProtocolDefinition, data: bytes) -> Layout:
    n = len(data)
    layout = Layout([], n)
    int_values: dict[str, int] = {}
    cursor = 0

    for idx, f in enumerate(protocol.fields):
        endian = f.endian or protocol.default_endian
        errors: list[str] = []
        warnings: list[str] = []

        def compute_start(size: int) -> int:
            if f.offset == "auto":
                return cursor
            if f.offset == "last":
                return n - size
            if isinstance(f.offset, int) and f.offset < 0:
                return n + f.offset
            return int(f.offset)

        if f.length == "remaining" and not f.length_from:
            start = compute_start(0)
            size = max(0, n - start - _tail_size(protocol.fields[idx + 1:]))
        else:
            if f.length_from:
                ref = int_values.get(f.length_from)
                if ref is None:
                    errors.append(
                        f"Length of '{f.name}' comes from '{f.length_from}', which could not be decoded"
                    )
                    size = 0
                else:
                    size = ref * f.length_scale + f.length_adjust
                    if size < 0:
                        errors.append(f"Length field '{f.length_from}' (= {ref}) gives a negative length for '{f.name}'")
                        size = 0
            else:
                fixed = f.fixed_size()
                if fixed is None:
                    errors.append(f"Length of '{f.name}' is undefined in the protocol definition")
                    size = 0
                else:
                    size = fixed
            start = compute_start(size)

        expected = size
        if start < 0:
            errors.append(
                f"Packet shorter than expected: '{f.name}' would start before the beginning of the packet"
            )
            start, size = 0, 0
        elif start > n:
            errors.append(f"Packet shorter than expected: '{f.name}' at offset {start} is beyond the packet end ({n} bytes)")
            start, size = n, 0
        elif start + size > n:
            available = n - start
            if f.length_from:
                errors.append(
                    f"Length field exceeds packet size: '{f.length_from}' = {int_values.get(f.length_from)} means "
                    f"'{f.name}' needs {expected} byte(s) at offset {start}, but only {available} remain"
                )
            elif available == 0:
                errors.append(f"Packet shorter than expected: '{f.name}' ({expected} byte(s)) is missing")
            else:
                errors.append(
                    f"Packet shorter than expected: '{f.name}' needs {expected} byte(s) at offset {start}, "
                    f"only {available} available"
                )
            size = available

        # Absolute offsets may overlap on purpose (e.g. a sub-field view);
        # relative placement overlapping means the packet is too short.
        absolute = isinstance(f.offset, int) and f.offset >= 0
        if size > 0 and start < cursor and not absolute:
            warnings.append(
                f"'{f.name}' at offset {start} overlaps the previous field (ends at {cursor - 1}) - "
                f"packet shorter than the length fields imply?"
            )

        sl = FieldSlice(f, start, size, expected, errors, warnings)
        layout.slices.append(sl)

        if size > 0 and size == expected and size <= 8 and f.is_integer:
            raw = data[start:start + size]
            int_values[f.name] = decode_int(raw, endian) if f.type.startswith("int") else decode_uint(raw, endian)

        cursor = max(cursor, start + size) if f.offset != "last" else start + size

    return layout
