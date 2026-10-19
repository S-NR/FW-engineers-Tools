"""Firmware Binary Comparison Tool.

A comparison utility for embedded firmware images (.bin, Intel .hex, .elf).
The comparison engine (``firmware_binary_tool.core``) is independent of the
GUI so that it can be reused by the CLI and by automated tests.
"""

__version__ = "1.0.0"
APP_NAME = "Firmware Binary Comparison Tool"
