"""Tests for block detection, region configuration and region analysis."""

import json
import unittest

from firmware_binary_tool.core.analysis import (
    ST_CHANGED, ST_EXPECTED, ST_NOT_PRESENT, ST_SAME, V_EXPECTED_ONLY, V_IDENTICAL, V_UNEXPECTED, analyze)
from firmware_binary_tool.core.binary_comparator import compare_images
from firmware_binary_tool.core.block_detector import EXPECTED, MIXED, UNEXPECTED, group_runs, sort_blocks
from firmware_binary_tool.core.intervals import IntervalSet
from firmware_binary_tool.core.region_manager import (
    MemoryRegion, ProjectConfig, parse_range_text, validate_regions)
from firmware_binary_tool.errors import ConfigError

from .helpers import TempDirTestCase, image_from_bytes

BASE = 0x08000000


def stm32_config(expected=()) -> ProjectConfig:
    return ProjectConfig(
        name="test",
        regions=[
            MemoryRegion("Bootloader", BASE, BASE + 0x7FFF),
            MemoryRegion("Application", BASE + 0x8000, BASE + 0x3FFFF),
            MemoryRegion("Calibration", BASE + 0x40000, BASE + 0x7FFFF),
        ],
        expected=list(expected),
    )


def make_pair(changes: dict[int, int], size=0x80000):
    a = bytearray(b"\xFF" * size)
    b = bytearray(a)
    for addr, val in changes.items():
        b[addr - BASE] = val
    return image_from_bytes(bytes(a), BASE), image_from_bytes(bytes(b), BASE)


class BlockTests(unittest.TestCase):
    def test_group_runs_merge_gap(self):
        diff = IntervalSet([(0, 2), (4, 6), (20, 21)])
        self.assertEqual(group_runs(diff, 0), [(0, 2), (4, 6), (20, 21)])
        self.assertEqual(group_runs(diff, 2), [(0, 6), (20, 21)])
        self.assertEqual(group_runs(diff, 100), [(0, 21)])
        with self.assertRaises(ValueError):
            group_runs(diff, -1)

    def test_blocks_with_regions_and_sorting(self):
        a, b = make_pair({BASE + 0x10: 0, BASE + 0x9000: 0, BASE + 0x9001: 0, BASE + 0x9002: 0})
        an = analyze(compare_images(a, b), stm32_config())
        self.assertEqual(len(an.blocks), 2)
        b1, b2 = an.blocks
        self.assertEqual((b1.start, b1.last, b1.size, b1.regions), (BASE + 0x10, BASE + 0x10, 1, ["Bootloader"]))
        self.assertEqual((b2.start, b2.size, b2.regions), (BASE + 0x9000, 3, ["Application"]))
        self.assertEqual(sort_blocks(an.blocks, "size")[0].start, BASE + 0x9000)
        self.assertEqual([x.regions[0] for x in sort_blocks(an.blocks, "region")], ["Application", "Bootloader"])
        self.assertEqual(an.largest_block.size, 3)

    def test_block_spanning_regions(self):
        a, b = make_pair({BASE + 0x7FFF: 0, BASE + 0x8000: 0})
        an = analyze(compare_images(a, b), stm32_config())
        self.assertEqual(an.blocks[0].regions, ["Bootloader", "Application"])

    def test_merge_gap_counts_changed_bytes(self):
        a, b = make_pair({BASE + 0x100: 0, BASE + 0x104: 0})
        cfg = stm32_config()
        cfg.merge_gap = 8
        an = analyze(compare_images(a, b), cfg)
        self.assertEqual(len(an.blocks), 1)
        self.assertEqual(an.blocks[0].size, 5)
        self.assertEqual(an.blocks[0].changed_bytes, 2)


class RegionAnalysisTests(unittest.TestCase):
    def test_region_table(self):
        changes = {BASE + 0x9000 + i: 0 for i in range(100)}
        a, b = make_pair(changes)
        an = analyze(compare_images(a, b), stm32_config())
        res = {r.region.name: r for r in an.region_results}
        self.assertEqual(res["Bootloader"].status, ST_SAME)
        self.assertEqual(res["Bootloader"].size, 32 * 1024)
        self.assertEqual(res["Application"].status, ST_CHANGED)
        self.assertEqual(res["Application"].changed_bytes, 100)
        self.assertAlmostEqual(res["Application"].changed_percent, 100 * 100 / (224 * 1024))
        self.assertEqual(res["Calibration"].status, ST_SAME)
        self.assertEqual(an.verdict, V_UNEXPECTED)

    def test_identical_verdict(self):
        a, b = make_pair({})
        an = analyze(compare_images(a, b), stm32_config())
        self.assertEqual(an.verdict, V_IDENTICAL)
        self.assertEqual(an.blocks, [])

    def test_expected_vs_unexpected(self):
        crc = MemoryRegion("CRC", BASE + 0x7FFFC, BASE + 0x7FFFF)
        version = MemoryRegion("Version", BASE + 0x1000, BASE + 0x101F)
        changes = {BASE + 0x7FFFC + i: 0 for i in range(4)}
        changes[BASE + 0x1000] = 0x31
        a, b = make_pair(changes)
        an = analyze(compare_images(a, b), stm32_config([crc, version]))
        self.assertEqual(an.verdict, V_EXPECTED_ONLY)
        self.assertEqual(an.expected_bytes, 5)
        self.assertEqual(an.unexpected_bytes, 0)
        res = {r.region.name: r for r in an.region_results}
        self.assertEqual(res["Calibration"].status, ST_EXPECTED)
        self.assertTrue(all(b.classification == EXPECTED for b in an.blocks))
        self.assertEqual({e.area.name: e.status for e in an.expected_results},
                         {"CRC": "CHANGED (EXPECTED)", "Version": "CHANGED (EXPECTED)"})

        # one more change just outside the CRC area -> MIXED block, unexpected verdict
        changes[BASE + 0x7FFFB] = 0
        a, b = make_pair(changes)
        an = analyze(compare_images(a, b), stm32_config([crc, version]))
        self.assertEqual(an.verdict, V_UNEXPECTED)
        self.assertEqual(an.unexpected_bytes, 1)
        blk = [x for x in an.blocks if x.start == BASE + 0x7FFFB][0]
        self.assertEqual(blk.classification, MIXED)
        self.assertEqual(len(an.unexpected_blocks), 1)

    def test_no_expected_areas_means_everything_unexpected(self):
        a, b = make_pair({BASE + 0x7FFFC: 0})
        an = analyze(compare_images(a, b), stm32_config())
        self.assertEqual(an.blocks[0].classification, UNEXPECTED)
        self.assertEqual(an.expected_bytes, 0)

    def test_changes_outside_regions_are_counted(self):
        a = image_from_bytes(b"\x00" * 0x100, BASE + 0x100000)
        b = image_from_bytes(b"\x01" * 0x100, BASE + 0x100000)
        an = analyze(compare_images(a, b), stm32_config())
        self.assertEqual(an.outside_changed, 0x100)
        self.assertTrue(all(r.status == ST_NOT_PRESENT for r in an.region_results))
        self.assertEqual(an.blocks[0].region_label, "(outside regions)")

    def test_size_mismatch_region(self):
        a = image_from_bytes(b"\xFF" * 0x40000, BASE)
        b = image_from_bytes(b"\xFF" * 0x80000, BASE)
        an = analyze(compare_images(a, b), stm32_config())
        res = {r.region.name: r for r in an.region_results}
        self.assertEqual(res["Calibration"].status, ST_CHANGED)
        self.assertEqual(res["Calibration"].changed_bytes, 0x40000)
        self.assertIn("A 0.0%", res["Calibration"].coverage_note)


class ConfigTests(TempDirTestCase):
    def test_parse_and_roundtrip(self):
        data = {"name": "x", "base_address": "0x08000000",
                "regions": [{"name": "Boot", "start": "0x08000000", "end": "0x08007FFF"},
                            {"name": "App", "start": 0x08008000, "size": "0x38000"}],
                "ignore": [{"name": "CRC", "start": "0x0807FFFC", "end": "0x0807FFFF"}]}
        cfg = ProjectConfig.from_dict(data)
        self.assertEqual(cfg.base_address, BASE)
        self.assertEqual(cfg.regions[1].end, 0x0803FFFF)
        self.assertEqual(cfg.expected[0].size, 4)
        p = self.path("cfg.json")
        cfg.save(p)
        again = ProjectConfig.load(p)
        self.assertEqual(again.to_dict(), cfg.to_dict())

    def test_validation_errors(self):
        with self.assertRaises(ConfigError):
            ProjectConfig.from_dict({"regions": [{"name": "Bad", "start": "0x100", "end": "0x10"}]})
        with self.assertRaises(ConfigError):
            ProjectConfig.from_dict({"regions": [{"name": "Bad", "start": "zz", "end": "0x10"}]})
        with self.assertRaises(ConfigError):
            ProjectConfig.from_dict({"regions": [{"start": "0", "end": "1"}]})
        with self.assertRaises(ConfigError):
            ProjectConfig.from_dict({"regions": [{"name": "A", "start": "0"}]})
        errors, warnings = validate_regions([MemoryRegion("A", 0, 10), MemoryRegion("a", 5, 20)])
        self.assertTrue(any("Duplicate" in e for e in errors))
        self.assertTrue(any("overlap" in w for w in warnings))
        cfg = ProjectConfig(regions=[MemoryRegion("A", 0, 10), MemoryRegion("A", 20, 30)])
        with self.assertRaises(ConfigError):
            cfg.check()

    def test_invalid_json_file(self):
        p = self.write("bad.json", "{not json")
        with self.assertRaises(ConfigError):
            ProjectConfig.load(p)

    def test_parse_range_text(self):
        self.assertEqual(parse_range_text("0x08000000", "+0x8000"), (BASE, BASE + 0x7FFF))
        self.assertEqual(parse_range_text("100", "200"), (100, 200))
        with self.assertRaises(ValueError):
            parse_range_text("0x10", "0x5")

    def test_bare_list(self):
        cfg = ProjectConfig.from_dict(json.loads('[{"name": "A", "start": "0", "end": "0xFF"}]'))
        self.assertEqual(len(cfg.regions), 1)


if __name__ == "__main__":
    unittest.main()
