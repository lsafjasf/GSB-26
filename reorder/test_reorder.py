"""消息重排缓冲：缺陷复现 + 修复回归测试。

运行：python3 -m unittest test_reorder -v
Part A（BuggyReproTest）：针对现网版本，稳定复现四类线上问题。
Part B（FixedRegressionTest）：修复版回归测试，含顺序/完整性断言、
                              回绕边界、缓冲上界与内存峰值测量。
"""

import random
import sys
import tracemalloc
import unittest

from reorder_buffer_buggy import BuggyReorderBuffer
from reorder_buffer import ReorderBuffer, seq_distance


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def assert_strictly_increasing(test, seqs, mod):
    """按序号算术验证交付序列严格递增（无重复、无回退）。"""
    for prev, cur in zip(seqs, seqs[1:]):
        d = seq_distance(prev, cur, mod)
        test.assertTrue(0 < d < mod // 2,
                        f"交付序列非严格递增: {prev} -> {cur}")


# ---------------------------------------------------------------------------
# Part A：复现现网四类缺陷（针对 BuggyReorderBuffer）
# ---------------------------------------------------------------------------

class BuggyReproTest(unittest.TestCase):
    def test_bug1_missing_seq_blocks_forever(self):
        """缺陷1：序号 5 长期缺失，6/7/8 被无限阻塞。"""
        buf = BuggyReorderBuffer()
        for i in range(5):
            buf.push(i, f"m{i}")
        delivered = []
        for i in (6, 7, 8):
            delivered += buf.push(i, f"m{i}")
        # 复现：后续消息全部卡住，没有任何交付
        self.assertEqual(delivered, [])
        self.assertEqual(len(buf._buf), 3)  # 消息积压，无限等待

    def test_bug2_duplicate_delivered_twice(self):
        """缺陷2：重复到达的消息被交付两次。"""
        buf = BuggyReorderBuffer()
        buf.push(0, "a")
        d1 = buf.push(1, "b")
        d2 = buf.push(1, "b")  # 重复到达
        self.assertEqual(d1, ["b"])
        # 复现：重复消息没有判重，再次交付
        self.assertEqual(d2, ["b"])

    def test_bug3_wraparound_breaks_ordering(self):
        """缺陷3：序号回绕时普通整数比较失效。"""
        buf = BuggyReorderBuffer()
        buf._next = 65534 % buf.mod
        buf.push(65534, "m65534")
        buf.push(65535, "m65535")
        # 回绕后：_next 变为 0，但旧序号 65535 用整数比较仍“大于”0
        d = buf.push(0, "m0")
        self.assertEqual(d, ["m0"])
        self.assertEqual(buf._next, 1)
        # 复现：重发回绕前的旧消息 65535，整数比较 65535 > 1，
        # 被误判为“未来”消息进入缓冲（判重失效，积压并终将重复交付）
        buf.push(65535, "m65535-dup")
        self.assertIn(65535, buf._buf)

    def test_bug4_timeout_release_redelivers(self):
        """缺陷4：超时释放后基线未推进，已交付消息再次交付。"""
        clock = FakeClock()
        buf = BuggyReorderBuffer(release_timeout=5.0, now=clock)
        for i in range(3):
            buf.push(i, f"m{i}")
        buf.push(5, "m5")          # 3,4 缺失
        clock.advance(6.0)
        d1 = buf.push(6, "m6")     # 触发超时释放
        self.assertEqual(d1, ["m5", "m6"])
        self.assertEqual(buf._next, 3)   # 基线未推进
        # 复现：已交付的 5 再次到达被重新缓冲，随后被第二次交付
        buf.push(5, "m5-dup")
        buf.push(3, "m3")
        d2 = buf.push(4, "m4")
        self.assertEqual(d2, ["m4", "m5-dup"])  # 5 被交付了两次


# ---------------------------------------------------------------------------
# Part B：修复版回归测试
# ---------------------------------------------------------------------------

class FixedRegressionTest(unittest.TestCase):
    def make_buf(self, **kw):
        clock = kw.pop("clock", None) or FakeClock()
        kw.setdefault("now", clock)
        return ReorderBuffer(**kw), clock

    # ---- 空洞与超时 ----

    def test_gap_timeout_reports_and_continues(self):
        buf, clock = self.make_buf(gap_timeout=5.0)
        for i in range(5):
            self.assertEqual(buf.push(i, f"m{i}"), [f"m{i}"])
        for i in (6, 7, 8):
            self.assertEqual(buf.push(i, f"m{i}"), [])  # 5 缺失，暂存
        clock.advance(5.0)
        delivered = buf.flush()
        self.assertEqual(delivered, ["m6", "m7", "m8"])
        # 明确报告缺口 [5, 5]
        self.assertEqual(len(buf.gaps), 1)
        self.assertEqual((buf.gaps[0].gap_start, buf.gaps[0].gap_end), (5, 5))
        # 迟到的 5 被判重丢弃，不会再次交付
        self.assertEqual(buf.push(5, "m5-late"), [])
        self.assertEqual(buf.duplicates, 1)
        # 后续消息正常交付
        self.assertEqual(buf.push(9, "m9"), ["m9"])

    def test_gap_timeout_via_push(self):
        """超时由后续 push 触发（无需显式 flush）。"""
        buf, clock = self.make_buf(gap_timeout=2.0)
        buf.push(0, "a")
        buf.push(2, "c")
        clock.advance(2.0)
        self.assertEqual(buf.push(3, "d"), ["c", "d"])
        self.assertEqual(len(buf.gaps), 1)

    # ---- 判重 ----

    def test_duplicate_delivered_once(self):
        buf, _ = self.make_buf()
        buf.push(0, "a")
        self.assertEqual(buf.push(1, "b"), ["b"])
        self.assertEqual(buf.push(1, "b"), [])   # 已交付的重复
        self.assertEqual(buf.push(0, "a"), [])
        buf.push(3, "d")
        self.assertEqual(buf.push(3, "d"), [])   # 缓冲中的重复
        self.assertEqual(buf.duplicates, 3)

    # ---- 乱序 + 完整性 ----

    def test_out_of_order_completeness(self):
        """0..N 随机打乱并注入重复，交付序列必须恰好是 0..N。"""
        n = 5000
        buf, clock = self.make_buf(gap_timeout=10.0, max_window=256)
        # 按 128 条分块、块内随机打乱（乱序幅度在接收窗口之内）
        rng = random.Random(42)
        order = []
        for base in range(0, n, 128):
            block = list(range(base, min(base + 128, n)))
            rng.shuffle(block)
            order.extend(block)
        delivered = []
        for seq in order:
            delivered += buf.push(seq, seq)
            if seq % 7 == 0:
                delivered += buf.push(seq, seq)  # 注入重复
            if seq % 50 == 0:
                clock.advance(1.0)
                delivered += buf.flush()
        clock.advance(20.0)
        delivered += buf.flush()
        self.assertEqual(delivered, list(range(n)))  # 完整性：不重不漏
        self.assertGreater(buf.duplicates, 0)

    # ---- 序号回绕 ----

    def test_wraparound_in_order_delivery(self):
        buf, _ = self.make_buf(mod_bits=16, start_seq=65533)
        expected = [65533, 65534, 65535, 0, 1, 2]
        got = []
        for seq in [65534, 0, 65533, 2, 65535, 1]:  # 跨回绕乱序
            got += buf.push(seq, seq)
        self.assertEqual(got, expected)
        assert_strictly_increasing(self, got, buf.mod)

    def test_wraparound_dedup(self):
        """回绕后，回绕前的旧序号重发必须被判重。"""
        buf, _ = self.make_buf(mod_bits=16, start_seq=65534)
        buf.push(65534, "a")
        buf.push(65535, "b")
        self.assertEqual(buf.push(0, "c"), ["c"])
        self.assertEqual(buf.push(65535, "b-dup"), [])  # 旧序号判重
        self.assertEqual(buf.push(65534, "a-dup"), [])
        self.assertEqual(buf.duplicates, 2)

    def test_wraparound_boundary_half_space(self):
        """边界：距离恰好为序号空间一半的序号判为“已交付侧”。"""
        buf, _ = self.make_buf(mod_bits=8, max_window=64, start_seq=100)
        half = 128
        # distance == half：按规则落在已交付窗口，判重丢弃
        self.assertEqual(buf.push((100 + half) % 256, "x"), [])
        self.assertEqual(buf.duplicates, 1)
        # distance == half - 1：在接收窗口之外（> max_window），溢出拒绝
        self.assertEqual(buf.push((100 + half - 1) % 256, "y"), [])
        self.assertEqual(buf.dropped, 1)
        # distance == max_window - 1：窗口内，正常缓冲
        buf.push((100 + 63) % 256, "z")
        self.assertIn((100 + 63) % 256, buf._buf)

    def test_wraparound_gap_skip_across_zero(self):
        """跨零点的空洞跳过：缺口报告与基线推进在回绕处正确。"""
        buf, clock = self.make_buf(mod_bits=16, start_seq=65534, gap_timeout=1.0)
        buf.push(65534, "a")
        buf.push(1, "d")           # 65535, 0 缺失
        clock.advance(1.5)
        self.assertEqual(buf.flush(), ["d"])
        self.assertEqual(len(buf.gaps), 1)
        gap = buf.gaps[0]
        self.assertEqual((gap.gap_start, gap.gap_end), (65535, 0))
        self.assertEqual(buf.push(2, "e"), ["e"])

    # ---- 超时释放不重发 ----

    def test_no_redelivery_after_timeout_release(self):
        buf, clock = self.make_buf(gap_timeout=3.0)
        for i in range(3):
            buf.push(i, i)
        buf.push(5, 5)
        clock.advance(4.0)
        self.assertEqual(buf.flush(), [5])
        # 已交付/已跳过的序号再次到达：全部判重，零交付
        for seq in (0, 1, 2, 3, 4, 5):
            self.assertEqual(buf.push(seq, seq), [])
        self.assertEqual(buf.duplicates, 6)

    # ---- 缓冲上界 ----

    def test_buffer_occupancy_bounded_reject(self):
        buf, clock = self.make_buf(max_window=64, gap_timeout=1000.0)
        buf.push(0, 0)              # 1 缺失，形成空洞
        sent = 0
        for seq in range(2, 2 + 500):
            buf.push(seq, seq)
            sent += 1
        self.assertLessEqual(len(buf._buf), 64)
        self.assertLessEqual(buf.max_occupancy, 64)
        # 窗口外的一律被拒绝：入窗 63 条（序号 1 的空洞占 1 个窗口位）
        self.assertEqual(buf.dropped, sent - len(buf._buf))
        self.assertEqual(len(buf._buf), 63)

    def test_buffer_occupancy_bounded_expire(self):
        """expire 策略：强制推进基线，交付仍严格递增且不重复。"""
        buf, _ = self.make_buf(max_window=16, gap_timeout=1e9,
                               on_full="expire")
        buf.push(0, 0)
        delivered = []
        for seq in range(2, 2 + 200):   # 1 永久缺失
            delivered += buf.push(seq, seq)
        assert_strictly_increasing(self, delivered, buf.mod)
        self.assertEqual(len(delivered), len(set(delivered)))  # 无重复
        self.assertLessEqual(buf.max_occupancy, 16)
        self.assertGreater(len(buf.gaps), 0)

    # ---- 内存峰值 ----

    def test_memory_peak(self):
        """10 万条消息（含空洞与乱序），缓冲条数与内存峰值均有界。"""
        buf, clock = self.make_buf(max_window=256, gap_timeout=5.0)
        rng = random.Random(7)
        tracemalloc.start()
        delivered = 0
        # 每 1000 条制造一个永久空洞，其余按窗口内乱序到达
        missing = set(range(500, 100_000, 1000))
        pending = []
        for seq in range(100_000):
            if seq in missing:
                continue
            pending.append(seq)
            if len(pending) >= 200:
                rng.shuffle(pending)
                for s in pending:
                    buf.push(s, b"x" * 64)
                    delivered += 0  # 交付计数在 flush 后统计
                pending.clear()
            if seq % 1000 == 0:
                clock.advance(6.0)
                buf.flush()
        clock.advance(6.0)
        buf.flush()
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        self.assertLessEqual(buf.max_occupancy, 256)
        # 峰值内存应与窗口上界同量级（宽松断言： < 8 MiB）
        self.assertLess(peak, 8 * 1024 * 1024)
        print(f"\n[内存数据] 缓冲占用峰值: {buf.max_occupancy} 条 "
              f"(上界 256); tracemalloc 内存峰值: {peak / 1024:.1f} KiB; "
              f"空洞跳过次数: {len(buf.gaps)}")


if __name__ == "__main__":
    unittest.main()
