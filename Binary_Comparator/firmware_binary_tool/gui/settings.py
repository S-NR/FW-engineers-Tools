"""Per-user GUI preferences: last folder, window size, recent comparisons.

Stored as JSON in the user's configuration folder:

* Windows: ``%APPDATA%/FirmwareBinaryTool/settings.json``
* Linux / macOS: ``$XDG_CONFIG_HOME/firmware_binary_tool/settings.json`` (``~/.config/...``)

Set ``FWCMP_SETTINGS_DIR`` to use another folder (used by the tests). Losing or
corrupting this file is harmless: defaults are used.
"""

from __future__ import annotations

import json
import os
import sys

MAX_RECENT = 8


def settings_dir() -> str:
    override = os.environ.get("FWCMP_SETTINGS_DIR")
    if override:
        return override
    if sys.platform == "win32":
        return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "FirmwareBinaryTool")
    base = os.environ.get("XDG_CONFIG_HOME", os.path.join(os.path.expanduser("~"), ".config"))
    return os.path.join(base, "firmware_binary_tool")


class Settings:
    def __init__(self) -> None:
        self.path = os.path.join(settings_dir(), "settings.json")
        self.data: dict = {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self.data = loaded
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
        except OSError:
            pass  # preferences are a convenience only

    # ------------------------------------------------------------------ values
    @property
    def last_dir(self) -> str:
        d = self.data.get("last_dir", "")
        return d if d and os.path.isdir(d) else os.getcwd()

    def remember_file(self, path: str) -> None:
        if path:
            self.data["last_dir"] = os.path.dirname(os.path.abspath(path))

    @property
    def geometry(self) -> str | None:
        g = self.data.get("geometry")
        return g if isinstance(g, str) else None

    @geometry.setter
    def geometry(self, value: str) -> None:
        self.data["geometry"] = value

    @property
    def recent(self) -> list[dict]:
        items = self.data.get("recent", [])
        return [r for r in items if isinstance(r, dict) and r.get("a") and r.get("b")]

    def add_recent(self, a: str, b: str, symbols_a: str | None = None, symbols_b: str | None = None,
                   config: str | None = None) -> None:
        entry = {"a": os.path.abspath(a), "b": os.path.abspath(b), "symbols_a": symbols_a, "symbols_b": symbols_b,
                 "config": config}
        items = [r for r in self.recent if (r["a"], r["b"]) != (entry["a"], entry["b"])]
        self.data["recent"] = [entry] + items[:MAX_RECENT - 1]


_instance: Settings | None = None


def get_settings() -> Settings:
    global _instance
    if _instance is None:
        _instance = Settings()
    return _instance
