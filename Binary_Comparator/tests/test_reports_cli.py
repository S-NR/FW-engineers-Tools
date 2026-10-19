"""Report generation, CLI behaviour and the hex viewer row model."""

import contextlib
import csv
import io
import json
import os
import unittest

from firmware_binary_tool.cli.cli import EXIT_DIFF, EXIT_ERROR, EXIT_OK, run
from firmware_binary_tool.core.intervals import IntervalSet
from firmware_binary_tool.core.region_manager import MemoryRegion, ProjectConfig
from firmware_binary_tool.core.session import load_and_compare
from firmware_binary_tool.gui.hex_model import HexRowModel
from firmware_binary_tool.reports.csv_report import HEADER, write_blocks_csv
from firmware_binary_tool.reports.html_report import render_html_report

from .helpers import TempDirTestCase

BASE = 0x08000000


class Fixture(TempDirTestCase):
    def setUp(self):
        super().setUp()
        old = bytearray(b"\xFF" * 0x10000)
        old[0x100:0x110] = b"FW_VERSION=1.0.0"
        new = bytearray(old)
        new[0x100:0x110] = b"FW_VERSION=1.0.1"
        new[0x8000:0x8010] = bytes(range(16))
        self.old = self.write("old.bin", bytes(old))
        self.new = self.write("new.bin", bytes(new))
        self.cfg_path = self.path("project.json")
        ProjectConfig(name="t", base_address=BASE,
                      regions=[MemoryRegion("Boot", BASE, BASE + 0x7FFF), MemoryRegion("App", BASE + 0x8000, BASE + 0xFFFF)],
                      expected=[MemoryRegion("Version", BASE + 0x100, BASE + 0x11F)]).save(self.cfg_path)


class ReportTests(Fixture):
    def test_html_and_csv(self):
        cfg = ProjectConfig.load(self.cfg_path)
        s = load_and_compare(self.old, self.new, cfg)
        try:
            html = render_html_report(s)
            for needle in ("Firmware A", "Firmware B", "SHA-256", "Overall comparison summary", "Region comparison",
                           "Changed blocks", "Expected / ignored changes", "Unexpected changes",
                           "Metadata differences", "Comparison timestamp", s.image_a.sha256, "UNEXPECTED CHANGES",
                           "0x08008000", "not guaranteed"):
                self.assertIn(needle, html)
            p = self.path("blocks.csv")
            n = write_blocks_csv(s, p)
            with open(p, newline="") as f:
                rows = list(csv.reader(f))
            self.assertEqual(rows[0], HEADER)
            self.assertEqual(len(rows) - 1, n)
            self.assertEqual(n, len(s.analysis.blocks))
            classes = {r[9] for r in rows[1:]}
            self.assertEqual(classes, {"EXPECTED", "UNEXPECTED"})
        finally:
            s.close()


class CliTests(Fixture):
    def _run(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stderr(io.StringIO()):
            code = run(list(args), out=out)
        return code, out.getvalue()

    def test_unexpected_changes_exit_code_and_outputs(self):
        report, csv_path, js = self.path("r.html"), self.path("c.csv"), self.path("s.json")
        code, out = self._run(self.old, self.new, "--regions", self.cfg_path, "--report", report,
                              "--csv", csv_path, "--json", js, "--checksum", "CRC-32")
        self.assertEqual(code, EXIT_DIFF)
        self.assertIn("Firmware Binary Comparison", out)
        self.assertIn("Changed bytes:", out)
        self.assertIn("Boot:", out)
        self.assertIn("App:", out)
        self.assertIn("Report generated:", out)
        self.assertTrue(os.path.getsize(report) > 1000)
        self.assertTrue(os.path.exists(csv_path))
        with open(js, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["verdict"], "UNEXPECTED CHANGES")
        self.assertEqual({r["name"]: r["status"] for r in data["regions"]},
                         {"Boot": "EXPECTED CHANGE", "App": "CHANGED"})

    def test_identical_exit_ok(self):
        code, out = self._run(self.old, self.old, "-q")
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(out.strip(), "IDENTICAL")

    def test_expected_only_exit_ok(self):
        exp = self.path("expected.json")
        with open(exp, "w") as f:
            json.dump({"expected": [{"name": "Ver", "start": "0x100", "end": "0x11F"},
                                    {"name": "App", "start": "0x8000", "size": "0x10"}]}, f)
        code, out = self._run(self.old, self.new, "--ignore", exp, "-q")
        self.assertEqual((code, out.strip()), (EXIT_OK, "EXPECTED CHANGES ONLY"))

    def test_errors(self):
        code, _ = self._run(self.old, self.path("missing.bin"))
        self.assertEqual(code, EXIT_ERROR)
        bad = self.write("bad.json", '{"regions": [{"name": "X", "start": "0x10", "end": "0x1"}]}')
        code, _ = self._run(self.old, self.new, "--regions", bad)
        self.assertEqual(code, EXIT_ERROR)

    def test_base_override(self):
        code, out = self._run(self.old, self.new, "--base", "0x08000000", "--blocks", "5")
        self.assertIn("0x08008000", out)


class HexModelTests(unittest.TestCase):
    def test_rows_and_lookup(self):
        m = HexRowModel(IntervalSet([(0x1005, 0x1015), (0x2000, 0x2001)]), 16)
        self.assertEqual(m.total_rows, 3)  # 0x1000, 0x1010, 0x2000
        self.assertEqual([m.row_to_addr(i) for i in range(3)], [0x1000, 0x1010, 0x2000])
        self.assertEqual(m.addr_to_row(0x1012), 1)
        self.assertEqual(m.addr_to_row(0x1800), 2)  # in a gap -> next row
        self.assertEqual(m.addr_to_row(0x9000), 2)  # beyond -> last row
        self.assertIsNone(m.row_to_addr(3))
        one = HexRowModel(IntervalSet([(10, 13)]), 1)
        self.assertEqual(one.total_rows, 3)
        self.assertEqual(one.row_to_addr(2), 12)

    def test_empty(self):
        m = HexRowModel(IntervalSet(), 16)
        self.assertEqual(m.total_rows, 0)
        self.assertIsNone(m.addr_to_row(0))


if __name__ == "__main__":
    unittest.main()
