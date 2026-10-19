"""Firmware Protocol Decoder - entry point.

    pip install -r requirements.txt
    python main.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make the package-less top-level modules (core, gui, ...) importable from anywhere.
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from gui import theme
    from gui.main_window import MainWindow
    from gui.settings import Settings
    from protocols.protocol_manager import ProtocolManager

    app = QApplication(sys.argv)
    app.setApplicationName("Firmware Protocol Decoder")
    app.setOrganizationName("protocol_decoder")

    settings = Settings()
    theme.apply_theme(app, settings.get("theme", "dark"))

    manager = ProtocolManager()
    manager.reload()

    window = MainWindow(settings, manager)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
