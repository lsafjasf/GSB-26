"""downsample 库的自测：口径、空洞、边界、乱序、重复、对拍。"""

import random
import unittest

from downsample import (
    AGGREGATORS,
    Aggregator,
    Bucket,
    DownsampleError,
    InvalidPointError,
    InvalidWindowError,
    MetricKind,
    MixedAggregationError,
    UnknownAggregatorError,
    downsample,
    naive_downsample,
    quantile,
)

ADDITIVE_AGGS = ["count", "sum"]
NON_ADDITIVE_AGGS = ["max", "avg", "p50", "p95"]


class TestSemantics(unittest.TestCase):
    def test_single_point(self):
        out = downsample([(100, 3.5)], 60, "sum")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0], Bucket(60, 120, {"sum": 3.5}))

    def test_single_window_multiple_points(self):
        pts = [(0, 1.0), (10, 2.0), (59, 3.0)]
        out = downsample(pts, 60, ["count", "sum"])
        self.assertEqual(out, [Bucket(0, 60, {"count": 3.0, "sum": 6.0})])

    def test_boundary_point_belongs_to_right_window(self):
        # t=60 恰好落在边界上：属于 [60,120)，不属于 [0,60)
        pts = [(59, 1.0), (60, 2.0), (119, 3.0), (120, 4.0)]
        out = downsample(pts, 60, "sum")
        self.assertEqual([b.values["sum"] for b in out], [1.0, 5.0, 4.0])

    def test_negative_timestamps_floor_alignment(self):
        # 负时间戳同样按 floor 对齐：t=-1 属于 [-60,0)
        out = downsample([(-1, 1.0), (0, 2.0)], 60, "sum")
        self.assertEqual([b.start for b in out], [-60, 0])
        self.assertEqual([b.values["sum"] for b in out], [1.0, 2.0])

    def test_gap_windows_are_none_not_zero(self):
        # 中间窗口无采样点：必须是空洞(None)，不得补零
        pts = [(0, 5.0), (180, 7.0)]
        out = downsample(pts, 60, "sum")
        self.assertEqual(len(out), 4)
        self.assertEqual(out[0].values, {"sum": 5.0})
        self.assertIsNone(out[1].values)
        self.assertTrue(out[1].empty)
        self.assertTrue(out[2].empty)
        self.assertEqual(out[3].values, {"sum": 7.0})

    def test_range_beyond_data_marks_empty(self):
        # 请求的时间范围超出数据范围：超出的窗口为空洞
        pts = [(70, 1.0)]
        out = downsample(pts, 60, "sum", start=0, end=240)
        self.assertEqual([b.empty for b in out], [True, False, True, True])
        self.assertEqual(out[1].values, {"sum": 1.0})

    def test_empty_input_with_range_gives_all_empty(self):
        out = downsample([], 60, "sum", start=0, end=180)
        self.assertEqual(len(out), 3)
        self.assertTrue(all(b.empty for b in out))

    def test_empty_input_without_range_gives_empty_list(self):
        self.assertEqual(downsample([], 60, "sum"), [])

    def test_many_windows(self):
        pts = [(i * 7, float(i)) for i in range(1000)]  # 跨 ~117 个窗口
        out = downsample(pts, 60, "count")
        self.assertEqual(len(out), (999 * 7) // 60 + 1)
        self.assertEqual(sum(b.values["count"] for b in out if not b.empty), 1000.0)

    def test_duplicate_timestamps_all_counted(self):
        pts = [(10, 1.0), (10, 2.0), (10, 3.0)]
        out = downsample(pts, 60, ["count", "sum"])
        self.assertEqual(out[0].values, {"count": 3.0, "sum": 6.0})

    def test_avg_and_max_and_quantile(self):
        pts = [(i, float(v)) for i, v in enumerate([1, 2, 3, 4])]
        out = downsample(pts, 60, ["max", "avg", "p50"])
        self.assertEqual(out[0].values["max"], 4.0)
        self.assertAlmostEqual(out[0].values["avg"], 2.5)
        self.assertAlmostEqual(out[0].values["p50"], 2.5)

    def test_quantile_known_value(self):
        # p90 of 1..10 (linear): rank = 0.9*9 = 8.1 -> 9 + 0.1*(10-9) = 9.1
        pts = [(i, float(i + 1)) for i in range(10)]
        out = downsample(pts, 60, "p90")
        self.assertAlmostEqual(out[0].values["p90"], 9.1)


class TestKindMixing(unittest.TestCase):
    def test_mixed_kinds_raise(self):
        with self.assertRaises(MixedAggregationError):
            downsample([(0, 1.0)], 60, ["sum", "max"])
        with self.assertRaises(MixedAggregationError):
            downsample([(0, 1.0)], 60, ["count", "avg"])
        with self.assertRaises(MixedAggregationError):
            naive_downsample([(0, 1.0)], 60, ["sum", "p95"])

    def test_same_kind_combinations_ok(self):
        downsample([(0, 1.0)], 60, ["count", "sum"])
        downsample([(0, 1.0)], 60, ["max", "avg", "p50", "p99"])

    def test_custom_aggregator_kind_enforced(self):
        custom = Aggregator("first", MetricKind.NON_ADDITIVE, lambda vs: float(vs[0]))
        with self.assertRaises(MixedAggregationError):
            downsample([(0, 1.0)], 60, ["sum", custom])

    def test_unknown_aggregator_raises(self):
        with self.assertRaises(UnknownAggregatorError):
            downsample([(0, 1.0)], 60, "median")


class TestValidation(unittest.TestCase):
    def test_invalid_window(self):
        for bad in (0, -5, 1.5, "60", None, True):
            with self.assertRaises(InvalidWindowError, msg=repr(bad)):
                downsample([(0, 1.0)], bad, "sum")

    def test_non_integer_timestamp(self):
        with self.assertRaises(InvalidPointError):
            downsample([(1.5, 1.0)], 60, "sum")

    def test_half_open_range(self):
        with self.assertRaises(DownsampleError):
            downsample([(0, 1.0)], 60, "sum", start=100, end=100)
        with self.assertRaises(DownsampleError):
            downsample([(0, 1.0)], 60, "sum", start=100)


class TestOrderIndependence(unittest.TestCase):
    def test_shuffled_input_same_result(self):
        rng = random.Random(42)
        base = [(rng.randrange(-500, 500), rng.uniform(-100, 100))
                for _ in range(2000)]
        # 加入重复时间戳
        base += [(0, 1.0)] * 5 + [(123, 2.5)] * 3
        expected = downsample(base, 60, ["count", "sum"])
        expected_na = downsample(base, 60, NON_ADDITIVE_AGGS)
        for trial in range(20):
            shuffled = base[:]
            random.Random(trial).shuffle(shuffled)
            self.assertEqual(downsample(shuffled, 60, ["count", "sum"]), expected)
            self.assertEqual(downsample(shuffled, 60, NON_ADDITIVE_AGGS), expected_na)


class TestCrossCheck(unittest.TestCase):
    """与逐点朴素聚合对拍：每个窗口的聚合值必须一致。"""

    def _check(self, pts, window, aggs, start=None, end=None):
        fast = downsample(pts, window, aggs, start=start, end=end)
        slow = naive_downsample(pts, window, aggs, start=start, end=end)
        self.assertEqual(len(fast), len(slow))
        for b_fast, b_slow in zip(fast, slow):
            self.assertEqual(b_fast.start, b_slow.start)
            self.assertEqual(b_fast.end, b_slow.end)
            if b_slow.empty:
                self.assertTrue(b_fast.empty, f"窗口 {b_fast.start} 应为空洞")
                continue
            self.assertFalse(b_fast.empty, f"窗口 {b_fast.start} 不应为空洞")
            for name, v in b_slow.values.items():
                self.assertAlmostEqual(
                    b_fast.values[name], v, places=9,
                    msg=f"窗口 {b_fast.start} 聚合 {name} 不一致",
                )

    def test_randomized_cross_check(self):
        for seed in range(30):
            rng = random.Random(seed)
            n = rng.randrange(0, 500)
            pts = [(rng.randrange(-1000, 1000), rng.uniform(-50, 50))
                   for _ in range(n)]
            # 人为制造重复时间戳与乱序
            pts += [(rng.randrange(-1000, 1000), 0.0) for _ in range(n // 10)]
            rng.shuffle(pts)
            window = rng.choice([1, 7, 60, 3600])
            self._check(pts, window, ADDITIVE_AGGS)
            self._check(pts, window, NON_ADDITIVE_AGGS)

    def test_cross_check_with_extended_range(self):
        rng = random.Random(7)
        pts = [(rng.randrange(0, 600), rng.uniform(0, 10)) for _ in range(200)]
        self._check(pts, 60, ADDITIVE_AGGS, start=-300, end=1200)
        self._check(pts, 60, NON_ADDITIVE_AGGS, start=-300, end=1200)

    def test_cross_check_window_larger_than_span(self):
        pts = [(3, 1.0), (9, 2.0)]
        self._check(pts, 10_000, ADDITIVE_AGGS)
        self._check(pts, 10_000, NON_ADDITIVE_AGGS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
