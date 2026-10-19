"""GUI entry point.

    python -m firmware_binary_tool [firmware_a] [firmware_b] [--config project.json]

Files given on the command line are pre-loaded (on Windows this also makes
"drop two files onto the program icon" work).
"""

from __future__ import annotations

import argparse
import os
import sys

if __package__ in (None, ""):  # executed as a script: make the package importable
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "firmware_binary_tool"


def main(argv: list[str] | None = None) -> int:
    from firmware_binary_tool import APP_NAME, __version__

    parser = argparse.ArgumentParser(prog="firmware-diff-gui", description=f"{APP_NAME} (GUI)")
    parser.add_argument("firmware_a", nargs="?", help="Firmware A (old)")
    parser.add_argument("firmware_b", nargs="?", help="Firmware B (new)")
    parser.add_argument("--config", "--regions", dest="config", help="Region / project configuration (.json)")
    parser.add_argument("--symbols-a", help="ELF or GNU ld .map file of build A (function-level changes)")
    parser.add_argument("--symbols-b", help="ELF or GNU ld .map file of build B")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)
    try:
        import tkinter  # noqa: F401
    except ImportError:
        print("ERROR: Tkinter is not available. On Debian/Ubuntu install it with 'sudo apt install python3-tk'; "
              "on Fedora 'sudo dnf install python3-tkinter'. The command-line tool 'firmware-diff' works "
              "without Tkinter.", file=sys.stderr)
        return 2
    from firmware_binary_tool.gui.main_window import run_gui
    run_gui(args.firmware_a, args.firmware_b, args.config, args.symbols_a, args.symbols_b)
    return 0


if __name__ == "__main__":
    sys.exit(main())
