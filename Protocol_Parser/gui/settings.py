"""Application settings stored in ``config/settings.json``."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from utils.file_utils import load_json, save_json

SETTINGS_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.json"

DEFAULTS: dict[str, Any] = {
    "theme": "dark",
    "last_protocol": "Dextens USB Protocol",
    "default_packet": "02 01 08 00 67 65 74 20 64 69 61 67 DD 1F 03",
    "hex_font_size": 11,
    "bytes_per_row": 16,
    "ai_backend": "heuristic",
    "auto_add_history": True,
    "last_input": None,
    "window_geometry": None,
    "window_state": None,
    "last_dir": "",
}


class Settings:
    def __init__(self, path: Path = SETTINGS_PATH) -> None:
        self.path = Path(path)
        self.load_error: str | None = None
        self._data = dict(DEFAULTS)
        try:
            stored = load_json(self.path, {})
            if isinstance(stored, dict):
                self._data.update(stored)
        except (ValueError, OSError) as exc:
            # A corrupt settings file must not prevent startup.
            self.load_error = str(exc)

    def get(self, key: str, default: Any = None) -> Any:
        value = self._data.get(key)
        return default if value is None else value

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def save(self) -> None:
        try:
            save_json(self.path, self._data)
        except OSError as exc:
            self.load_error = f"Cannot save settings: {exc}"
