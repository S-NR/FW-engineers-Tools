"""Loading, listing and saving protocol definition JSON files.

Built-in examples live in ``protocols/examples``; protocols created or saved
by the user go to ``protocols/user``. A user protocol with the same name as
an example overrides it.
"""
from __future__ import annotations

import re
from pathlib import Path

from core.protocol import ProtocolDefinition, ProtocolError
from utils.file_utils import load_json, save_json

__all__ = ["ProtocolManager", "EXAMPLES_DIR", "USER_DIR"]

PROTOCOLS_DIR = Path(__file__).resolve().parent
EXAMPLES_DIR = PROTOCOLS_DIR / "examples"
USER_DIR = PROTOCOLS_DIR / "user"


def _slug(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").lower()
    return slug or "protocol"


class ProtocolManager:
    def __init__(self, search_dirs: list[Path] | None = None, user_dir: Path | None = None) -> None:
        self.user_dir = Path(user_dir) if user_dir else USER_DIR
        self.search_dirs = [Path(d) for d in search_dirs] if search_dirs else [EXAMPLES_DIR, self.user_dir]
        self._protocols: dict[str, ProtocolDefinition] = {}
        self.load_errors: list[str] = []

    # ----------------------------------------------------------- loading
    def reload(self) -> None:
        self._protocols.clear()
        self.load_errors.clear()
        for directory in self.search_dirs:
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.json")):
                try:
                    proto = self.load_file(path)
                except (ProtocolError, ValueError, OSError) as exc:
                    self.load_errors.append(f"{path.name}: {exc}")
                    continue
                self._protocols[proto.name] = proto

    @staticmethod
    def load_file(path: str | Path) -> ProtocolDefinition:
        data = load_json(path)
        if data is None:
            raise ProtocolError(f"File not found: {path}")
        proto = ProtocolDefinition.from_dict(data)
        proto.source_path = str(path)
        return proto

    def import_file(self, path: str | Path) -> ProtocolDefinition:
        """Load a protocol from anywhere and register it for this session."""
        proto = self.load_file(path)
        self._protocols[proto.name] = proto
        return proto

    # ------------------------------------------------------------ access
    def names(self) -> list[str]:
        return list(self._protocols)

    def get(self, name: str) -> ProtocolDefinition | None:
        return self._protocols.get(name)

    def all(self) -> list[ProtocolDefinition]:
        return list(self._protocols.values())

    # ------------------------------------------------------------ saving
    def save(self, proto: ProtocolDefinition, path: str | Path | None = None,
             previous_name: str | None = None) -> Path:
        """Save to ``path``, the protocol's source file, or a new file in the user dir."""
        if path is None:
            path = proto.source_path or self.user_dir / f"{_slug(proto.name)}.json"
        path = Path(path)
        save_json(path, proto.to_dict())
        proto.source_path = str(path)
        if previous_name and previous_name != proto.name:
            self._protocols.pop(previous_name, None)
        self._protocols[proto.name] = proto
        return path

    def new_protocol_path(self, name: str) -> Path:
        return self.user_dir / f"{_slug(name)}.json"
