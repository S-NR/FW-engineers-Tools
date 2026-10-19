"""Unit tests for the byte comparison engine."""

import random
import threading
import unittest

from firmware_binary_tool.core.binary_comparator import (
    CHUNK, DIFF, NO_DATA, ONLY_A, ONLY_B, SAME, compare_images, count_diff_bytes, diff_buffers)
from firmware_binary_tool.errors import ComparisonCancelled

from .helpers import image_from_bytes, image_from_segments


def naive_diff(a: bytes, b: bytes) -> set[int]:
    n = min(len(a), len(b))
    out = {i for i in range(n) if a[i] != b[i]}
    out.update(range(n, max(len(a), len(b))))
    return out


def addresses(iv) -> set[int]:
    return {x for s, e in iv for x in range(s, e)}


class ComparatorTests(unittest.TestCase):
    def test_identical(self):
        data = bytes(range(256)) * 100
        r = compare_images(image_from_bytes(data), image_from_bytes(data))
        self.assertTrue(r.identical)
        self.assertEqual(r.diff_bytes, 0)
        self.assertEqual(r.total_compared, len(data))
        self.assertIsNone(r.first_diff)
        self.assertEqual(r.changed_percent, 0.0)

    def test_single_byte_difference_with_base_address(self):
        a = bytearray(1024)
        b = bytearray(1024)
        b[0x241] = 0x27
        r = compare_images(image_from_bytes(bytes(a), 0x08012800), image_from_bytes(bytes(b), 0x08012800))
        self.assertEqual(r.diff_bytes, 1)
        self.assertEqual(r.first_diff, 0x08012A41)
        self.assertEqual(r.last_diff, 0x08012A41)
        self.assertEqual(r.status_at(0x08012A41), DIFF)
        self.assertEqual(r.status_at(0x08012A40), SAME)
        self.assertEqual(r.status_at(0x0), NO_DATA)

    def test_runs_are_contiguous_blocks(self):
        a = bytearray(4096)
        b = bytearray(a)
        for i in list(range(100, 110)) + [200] + list(range(4000, 4096)):
            b[i] = 0xFF
        r = compare_images(image_from_bytes(bytes(a)), image_from_bytes(bytes(b)))
        self.assertEqual(list(r.diff), [(100, 110), (200, 201), (4000, 4096)])
        self.assertEqual(r.diff_bytes, 10 + 1 + 96)

    def test_runs_merge_across_chunk_boundaries(self):
        size = CHUNK * 2 + 100
        a = bytes(size)
        b = bytearray(a)
        for i in range(CHUNK - 5, CHUNK + 5):
            b[i] = 1
        r = compare_images(image_from_bytes(a), image_from_bytes(bytes(b)))
        self.assertEqual(list(r.diff), [(CHUNK - 5, CHUNK + 5)])

    def test_random_against_naive(self):
        rnd = random.Random(42)
        for trial in range(30):
            n = rnd.randint(0, 9000)
            a = bytes(rnd.getrandbits(8) for _ in range(n))
            b = bytearray(a)
            for _ in range(rnd.randint(0, 50)):
                if n:
                    pos = rnd.randrange(n)
                    for k in range(pos, min(n, pos + rnd.randint(1, 40))):
                        b[k] = rnd.getrandbits(8)
            if trial % 3 == 0:
                b = b[:rnd.randint(0, n)] if n else b
            elif trial % 3 == 1:
                b += bytes(rnd.getrandbits(8) for _ in range(rnd.randint(0, 300)))
            b = bytes(b)
            r = compare_images(image_from_bytes(a), image_from_bytes(b))
            self.assertEqual(addresses(r.diff), naive_diff(a, b), f"trial {trial}")
            self.assertEqual(count_diff_bytes(a, b), len(naive_diff(a, b)))

    def test_different_sizes_are_reported(self):
        a = bytes(100)
        b = bytes(150)
        r = compare_images(image_from_bytes(a), image_from_bytes(b))
        self.assertEqual(r.total_compared, 100)
        self.assertEqual(r.only_b_bytes, 50)
        self.assertEqual(r.only_a_bytes, 0)
        self.assertEqual(r.content_diff_bytes, 0)
        self.assertEqual(r.diff_bytes, 50)
        self.assertEqual(r.union_bytes, 150)
        self.assertAlmostEqual(r.changed_percent, 100 * 50 / 150)
        self.assertEqual(r.status_at(120), ONLY_B)
        r2 = compare_images(image_from_bytes(b), image_from_bytes(a))
        self.assertEqual(r2.only_a_bytes, 50)
        self.assertEqual(r2.status_at(120), ONLY_A)

    def test_empty_images(self):
        r = compare_images(image_from_bytes(b""), image_from_bytes(b""))
        self.assertTrue(r.identical)
        self.assertEqual(r.union_bytes, 0)
        self.assertEqual(r.changed_percent, 0.0)
        r = compare_images(image_from_bytes(b""), image_from_bytes(b"\x01\x02"))
        self.assertEqual(r.diff_bytes, 2)
        self.assertEqual(r.only_b_bytes, 2)

    def test_sparse_segments(self):
        a = image_from_segments([(0x1000, b"\x00" * 16), (0x2000, b"\x11" * 16)])
        b = image_from_segments([(0x1000, b"\x00" * 8 + b"\xFF" + b"\x00" * 7), (0x3000, b"\x22" * 4)])
        r = compare_images(a, b)
        self.assertEqual(r.total_compared, 16)
        self.assertEqual(r.content_diff_bytes, 1)
        self.assertEqual(r.only_a_bytes, 16)
        self.assertEqual(r.only_b_bytes, 4)
        self.assertEqual(r.diff_bytes, 21)
        self.assertEqual(r.status_at(0x1008), DIFF)
        self.assertEqual(r.status_at(0x2500), NO_DATA)

    def test_adjacent_segments_in_one_image(self):
        a = image_from_segments([(0, b"\x00" * 10), (10, b"\x00" * 10)])
        b = image_from_bytes(b"\x00" * 9 + b"\x01\x01" + b"\x00" * 9)
        r = compare_images(a, b)
        self.assertEqual(list(r.diff), [(9, 11)])

    def test_progress_and_cancel(self):
        data = bytes(CHUNK * 4)
        calls = []
        compare_images(image_from_bytes(data), image_from_bytes(data), progress=lambda d, t: calls.append((d, t)))
        self.assertEqual(calls[-1], (len(data), len(data)))
        ev = threading.Event()
        ev.set()
        with self.assertRaises(ComparisonCancelled):
            compare_images(image_from_bytes(data), image_from_bytes(data), cancel=ev)

    def test_fill_mode_compares_one_sided_bytes(self):
        # sparse HEX-like image vs full dump with erased (0xFF) gap
        a = image_from_segments([(0, b"\x01\x02"), (6, b"\x07\x08")])
        b = image_from_bytes(b"\x01\x02\xFF\xFF\x55\xFF\x07\x08")
        strict = compare_images(a, b)
        self.assertEqual(strict.only_b_bytes, 4)
        filled = compare_images(a, b, fill=0xFF)
        self.assertEqual(list(filled.diff), [(4, 5)])  # only the 0x55 byte differs from erased flash
        self.assertEqual(filled.only_b_bytes, 1)
        self.assertEqual(filled.total_compared, 8)
        self.assertEqual(filled.status_at(2), "SAME (fill)")
        self.assertEqual(filled.status_at(4), ONLY_B)
        with self.assertRaises(ValueError):
            compare_images(a, b, fill=0x100)

    def test_diff_buffers_base(self):
        self.assertEqual(list(diff_buffers(b"\x00\x01", b"\x00\x02", base=100)), [(101, 102)])


if __name__ == "__main__":
    unittest.main()
