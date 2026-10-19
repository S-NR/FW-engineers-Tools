import random
import unittest

from firmware_binary_tool.core.intervals import IntervalSet, merge_intervals


def as_set(iv: IntervalSet) -> set[int]:
    return {a for s, e in iv for a in range(s, e)}


class IntervalSetTests(unittest.TestCase):
    def test_merge_adjacent_and_overlapping(self):
        self.assertEqual(merge_intervals([(5, 10), (0, 5), (8, 12), (20, 20), (30, 25)]), [(0, 12)])

    def test_total_first_last(self):
        s = IntervalSet([(10, 20), (30, 31)])
        self.assertEqual(s.total, 11)
        self.assertEqual(s.first, 10)
        self.assertEqual(s.last, 30)
        self.assertIsNone(IntervalSet().first)

    def test_overlap(self):
        s = IntervalSet([(10, 20), (30, 40)])
        self.assertEqual(s.overlap(0, 100), 20)
        self.assertEqual(s.overlap(15, 35), 10)
        self.assertEqual(s.overlap(20, 30), 0)
        self.assertEqual(s.overlap(12, 13), 1)
        self.assertEqual(s.overlap(39, 41), 1)
        self.assertEqual(s.overlap(5, 5), 0)

    def test_set_algebra_matches_python_sets(self):
        rnd = random.Random(1234)
        for _ in range(200):
            a = IntervalSet((x, x + rnd.randint(1, 8)) for x in rnd.sample(range(100), 10))
            b = IntervalSet((x, x + rnd.randint(1, 8)) for x in rnd.sample(range(100), 10))
            sa, sb = as_set(a), as_set(b)
            self.assertEqual(as_set(a.intersect(b)), sa & sb)
            self.assertEqual(as_set(a.subtract(b)), sa - sb)
            self.assertEqual(as_set(a.union(b)), sa | sb)
            lo, hi = sorted(rnd.sample(range(110), 2))
            self.assertEqual(a.overlap(lo, hi), len([x for x in sa if lo <= x < hi]))
            for x in range(0, 110, 7):
                self.assertEqual(a.contains(x), x in sa)

    def test_navigation(self):
        s = IntervalSet([(10, 20), (30, 40)])
        self.assertEqual(s.next_start_after(10), 30)
        self.assertIsNone(s.next_start_after(30))
        self.assertEqual(s.prev_start_before(30), 10)
        self.assertEqual(s.prev_start_before(35), 30)
        self.assertEqual(s.first_at_or_after(25), 30)
        self.assertEqual(s.first_at_or_after(15), 15)
        self.assertEqual(list(s.clip(15, 35)), [(15, 20), (30, 35)])


if __name__ == "__main__":
    unittest.main()
