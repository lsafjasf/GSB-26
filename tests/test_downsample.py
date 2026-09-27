"""降采样库测试：口径、空洞、边界、乱序/重复、对拍、边界情形。"""
import math
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from downsample import (
    Avg,
    Count,
    Max,
    MixedAggregationError,
    Quantile,
    Sum,
    downsample,
    merge_states,
    naive_downsample,
)

ALL_AGGS = [Sum(), Count(), Max(), Avg(), Quantile(0.5), Quantile(0.99)]


def make_points(seed=42, n=5000, t_lo=-1000, t_hi=100000, dup=True):
    rng = random.Random(seed)
    pts = [(rng.randint(t_lo, t_hi), rng.uniform(-100, 100)) for _ in range(n)]
    if dup:  # 注入重复时间戳
        for _ in range(n // 10):
            t, _ = pts[rng.randrange(len(pts))]
            pts.append((t, rng.uniform(-100, 100)))
    return pts


class TestSemantics(unittest.TestCase):
    def test_sum_basic(self):
        pts = [(0, 1.0), (1, 2.0), (59, 3.0), (60, 10.0)]
        out = downsample(pts, 60, Sum())
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0].value, 6.0)
        self.assertEqual(out[0].count, 3)
        self.assertEqual(out[1].value, 10.0)

    def test_count(self):
        pts = [(5, 1.0), (5, 2.0), (7, 3.0)]
        out = downsample(pts, 60, Count())
        self.assertEqual(out[0].value, 3.0)

    def test_max_avg_quantile(self):
        pts = [(i, float(v)) for i, v in enumerate([1, 5, 2, 8, 4])]
        self.assertEqual(downsample(pts, 60, Max())[0].value, 8.0)
        self.assertEqual(downsample(pts, 60, Avg())[0].value, 4.0)
        # 排序后 [1,2,4,5,8]，p50 线性插值 -> 4
        self.assertEqual(downsample(pts, 60, Quantile(0.5))[0].value, 4.0)
        # p25: pos = 0.25*4 = 1 -> 2
        self.assertEqual(downsample(pts, 60, Quantile(0.25))[0].value, 2.0)

    def test_quantile_invalid_q(self):
        with self.assertRaises(ValueError):
            Quantile(1.5)

    def test_invalid_window_and_range(self):
        with self.assertRaises(ValueError):
            downsample([(0, 1.0)], 0, Sum())
        with self.assertRaises(ValueError):
            downsample([(0, 1.0)], 60, Sum(), start=100, end=100)


class TestMixedAggregation(unittest.TestCase):
    def test_merge_different_aggregators_raises(self):
        with self.assertRaises(MixedAggregationError):
            merge_states(Sum(), [1.0], Count(), [1])
        with self.assertRaises(MixedAggregationError):
            merge_states(Sum(), [1.0], Max(), [1.0])

    def test_merge_different_params_raises(self):
        with self.assertRaises(MixedAggregationError):
            merge_states(Quantile(0.5), [1.0], Quantile(0.9), [1.0])

    def test_merge_non_additive_raises(self):
        for agg in (Max(), Avg(), Quantile(0.5)):
            with self.assertRaises(MixedAggregationError):
                merge_states(agg, [1.0], agg, [2.0])

    def test_merge_additive_ok(self):
        merged = merge_states(Sum(), [1.0, 2.0], Sum(), [3.0])
        self.assertEqual(Sum().finalize(merged), 6.0)
        merged = merge_states(Count(), [2], Count(), [3])
        self.assertEqual(Count().finalize(merged), 5.0)


class TestGaps(unittest.TestCase):
    def test_empty_window_is_none_not_zero(self):
        pts = [(0, 5.0), (300, 7.0)]  # 中间窗口 60..300 无数据
        out = downsample(pts, 60, Sum())
        self.assertEqual(len(out), 6)
        self.assertEqual(out[0].value, 5.0)
        for b in out[1:5]:
            self.assertIsNone(b.value, "空洞窗口必须为 None，不得静默补零")
            self.assertEqual(b.count, 0)
        self.assertEqual(out[5].value, 7.0)

    def test_empty_windows_for_all_aggregators(self):
        pts = [(0, 5.0), (180, 7.0)]
        for agg in ALL_AGGS:
            out = downsample(pts, 60, agg)
            self.assertIsNone(out[1].value, agg.name)
            self.assertEqual(out[1].count, 0, agg.name)

    def test_range_beyond_data(self):
        pts = [(120, 1.0), (130, 2.0)]
        out = downsample(pts, 60, Sum(), start=0, end=300)
        self.assertEqual(len(out), 5)
        self.assertIsNone(out[0].value)  # 数据范围之前
        self.assertEqual(out[2].value, 3.0)
        self.assertIsNone(out[4].value)  # 数据范围之后

    def test_empty_input(self):
        self.assertEqual(downsample([], 60, Sum()), [])
        out = downsample([], 60, Sum(), start=0, end=120)
        self.assertEqual(len(out), 2)
        self.assertTrue(all(b.value is None for b in out))


class TestBoundaries(unittest.TestCase):
    def test_boundary_point_goes_right(self):
        # 左闭右开：t=60 属于窗口 [60,120)
        pts = [(59, 1.0), (60, 2.0), (119, 3.0), (120, 4.0)]
        out = downsample(pts, 60, Sum())
        self.assertEqual(out[0].value, 1.0)
        self.assertEqual(out[1].value, 5.0)
        self.assertEqual(out[2].value, 4.0)

    def test_unaligned_start_floored_to_grid(self):
        # start=30 落在窗口 [0,60) 内，输出仍按绝对网格对齐
        pts = [(30, 1.0)]
        out = downsample(pts, 60, Sum(), start=30, end=90)
        self.assertEqual((out[0].start, out[0].end), (0, 60))
        self.assertEqual((out[1].start, out[1].end), (60, 120))

    def test_negative_timestamps(self):
        # 地板除一致性：t=-1 属于窗口 [-60,0)
        pts = [(-1, 1.0), (0, 2.0), (-60, 3.0)]
        out = downsample(pts, 60, Sum())
        self.assertEqual(out[0].start, -60)
        self.assertEqual(out[0].value, 4.0)  # -60 与 -1 同窗
        self.assertEqual(out[1].value, 2.0)

    def test_duplicate_timestamps_all_counted(self):
        pts = [(10, 1.0), (10, 2.0), (10, 3.0)]
        out = downsample(pts, 60, Sum())
        self.assertEqual(out[0].value, 6.0)
        self.assertEqual(out[0].count, 3)
        out = downsample(pts, 60, Avg())
        self.assertEqual(out[0].value, 2.0)


class TestOrderIndependence(unittest.TestCase):
    def test_shuffles_give_identical_results(self):
        pts = make_points(seed=7, n=3000)
        for agg in ALL_AGGS:
            reference = downsample(pts, 60, agg)
            for seed in range(5):
                shuffled = pts[:]
                random.Random(seed).shuffle(shuffled)
                out = downsample(shuffled, 60, agg)
                self.assertEqual(
                    reference, out, f"{agg.name} 对到达顺序不稳定 (seed={seed})"
                )

    def test_sum_bit_exact_under_shuffle(self):
        # fsum 保证浮点求和逐位一致
        pts = [(i % 100, 0.1 * (i % 7)) for i in range(10000)]
        a = downsample(pts, 10, Sum())
        b = downsample(list(reversed(pts)), 10, Sum())
        self.assertEqual([x.value for x in a], [x.value for x in b])


class TestCrossCheckNaive(unittest.TestCase):
    def test_matches_naive_per_bucket(self):
        # 朴素实现为 O(窗口数 x 点数)，窗口数控制在数千以内
        pts = make_points(seed=123, n=2000, t_lo=-1000, t_hi=20000)
        for window in (7, 60, 1000):
            for agg in ALL_AGGS:
                fast = downsample(pts, window, agg, start=-1200, end=21000)
                slow = naive_downsample(pts, window, agg, start=-1200, end=21000)
                self.assertEqual(len(fast), len(slow))
                for f, s in zip(fast, slow):
                    self.assertEqual((f.start, f.end), (s.start, s.end))
                    self.assertEqual(f.count, s.count, f"{agg.name}@{f.start}")
                    if s.value is None:
                        self.assertIsNone(f.value)
                    else:
                        self.assertEqual(
                            f.value, s.value, f"{agg.name}@{f.start} w={window}"
                        )


class TestEdgeCases(unittest.TestCase):
    def test_single_point(self):
        out = downsample([(12345, 9.0)], 60, Sum())
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].value, 9.0)
        self.assertEqual(out[0].start, (12345 // 60) * 60)

    def test_single_window(self):
        pts = [(i, float(i)) for i in range(60)]
        out = downsample(pts, 60, Sum())
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].count, 60)

    def test_many_windows_sparse(self):
        # 跨越 100000 个窗口，只有少量点
        pts = [(i * 60, 1.0) for i in range(0, 100000, 9973)]
        out = downsample(pts, 60, Sum(), start=0, end=100000 * 60)
        self.assertEqual(len(out), 100000)
        filled = [b for b in out if b.value is not None]
        self.assertEqual(len(filled), len(pts))
        self.assertTrue(all(b.value == 1.0 for b in filled))

    def test_point_at_end_excluded(self):
        # end 为开区间：t=120 不属于 [0,120)
        pts = [(0, 1.0), (120, 2.0)]
        out = downsample(pts, 60, Sum(), start=0, end=120)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0].value, 1.0)
        self.assertIsNone(out[1].value)


if __name__ == "__main__":
    unittest.main(verbosity=2)
