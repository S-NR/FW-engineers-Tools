#!/usr/bin/env python3
"""GUI launcher: python firmware_compare_gui.py [firmware_a] [firmware_b] [--config project.json]"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from firmware_binary_tool.main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
