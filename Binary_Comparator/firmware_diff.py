#!/usr/bin/env python3
"""Command-line launcher: python firmware_diff.py old.bin new.bin [options]"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from firmware_binary_tool.cli.cli import main_entry  # noqa: E402

if __name__ == "__main__":
    main_entry()
