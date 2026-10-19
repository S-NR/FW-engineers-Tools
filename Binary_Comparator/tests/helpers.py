"""Shared test helpers."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from firmware_binary_tool.core.image import FirmwareImage, Segment


def image_from_bytes(data: bytes, base: int = 0, name: str = "mem.bin") -> FirmwareImage:
    segs = [Segment(base, bytes(data), 0)] if data else []
    return FirmwareImage(name, name, "bin", len(data), "", segs, base_address=base)


def image_from_segments(segments: list[tuple[int, bytes]], name: str = "mem.hex") -> FirmwareImage:
    return FirmwareImage(name, name, "hex", 0, "", [Segment(a, bytes(d)) for a, d in segments])


class TempDirTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="fwcmp_test_")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def path(self, name: str) -> str:
        return os.path.join(self.tmp, name)

    def write(self, name: str, data: bytes | str) -> str:
        p = self.path(name)
        mode = "w" if isinstance(data, str) else "wb"
        with open(p, mode) as f:
            f.write(data)
        return p
