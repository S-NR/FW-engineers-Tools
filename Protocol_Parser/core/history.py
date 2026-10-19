"""Session packet history with CSV export."""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from datetime import datetime

from utils.hex_utils import format_hex

__all__ = ["HistoryEntry", "PacketHistory"]


@dataclass
class HistoryEntry:
    timestamp: datetime
    direction: str
    data: bytes
    status: str
    protocol: str
    summary: str = ""

    @property
    def length(self) -> int:
        return len(self.data)

    @property
    def time_text(self) -> str:
        return self.timestamp.strftime("%H:%M:%S")


class PacketHistory:
    COLUMNS = ["Time", "Direction", "Length", "Status", "Protocol", "Summary", "Data"]

    def __init__(self, max_entries: int = 10000) -> None:
        self.max_entries = max_entries
        self._entries: list[HistoryEntry] = []

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self):
        return iter(self._entries)

    def __getitem__(self, index: int) -> HistoryEntry:
        return self._entries[index]

    def add(self, direction: str, data: bytes, status: str, protocol: str, summary: str = "",
            timestamp: datetime | None = None) -> HistoryEntry:
        entry = HistoryEntry(timestamp or datetime.now(), direction or "-", bytes(data), status, protocol, summary)
        self._entries.append(entry)
        if len(self._entries) > self.max_entries:
            del self._entries[0: len(self._entries) - self.max_entries]
        return entry

    def clear(self) -> None:
        self._entries.clear()

    def export_csv(self, path: str | os.PathLike) -> int:
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(self.COLUMNS)
            for e in self._entries:
                writer.writerow([e.timestamp.isoformat(timespec="milliseconds"), e.direction, e.length,
                                 e.status, e.protocol, e.summary, format_hex(e.data)])
        return len(self._entries)
