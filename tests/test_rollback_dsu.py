"""RollbackDSU 单元自测：python3 -m unittest discover -s tests -v"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from rollback_dsu import RollbackDSU


class TestEdgeCases(unittest.TestCase):
    def test_empty_structure(self):
        d = RollbackDSU(0)
        self.assertEqual(len(d), 0)
        self.assertEqual(d.num_components, 0)
        cp = d.checkpoint()
        d.rollback(cp)          # 空结构上回滚是合法无操作
        self.assertEqual(d.partition(), {})

    def test_single_element(self):
        d = RollbackDSU(1)
        self.assertEqual(d.find(0), 0)
        self.assertFalse(d.union(0, 0))   # 自合并不生效、不写日志
        self.assertEqual(d.checkpoint(), 0)
        self.assertEqual(d.component_size(0), 1)
        self.assertEqual(d.num_components, 1)

    def test_rollback_to_origin_repeatedly(self):
        d = RollbackDSU(8)
        cp0 = d.checkpoint()
        for _ in range(3):                # 连续回滚到起点
            for i in range(7):
                d.union(i, i + 1)
            self.assertEqual(d.num_components, 1)
            d.rollback(cp0)
            self.assertEqual(d.num_components, 8)
            self.assertEqual(d.partition(), {i: [i] for i in range(8)})
        d.rollback(cp0)                   # 空日志再回滚仍合法
        self.assertEqual(d.num_components, 8)

    def test_rollback_to_same_checkpoint_twice(self):
        d = RollbackDSU(6)
        d.union(0, 1)
        cp = d.checkpoint()
        d.union(2, 3)
        d.union(3, 4)
        d.rollback(cp)
        self.assertEqual(d.partition(), {0: [0, 1], 2: [2], 3: [3], 4: [4], 5: [5]})
        d.rollback(cp)                    # 第二次回滚到同一检查点：无操作
        self.assertEqual(d.partition(), {0: [0, 1], 2: [2], 3: [3], 4: [4], 5: [5]})
        # 回滚后还能继续合并，且新日志不被旧检查点干扰
        d.union(4, 5)
        self.assertTrue(d.connected(4, 5))
        d.rollback(cp)
        self.assertFalse(d.connected(4, 5))
        self.assertTrue(d.connected(0, 1))

    def test_deep_chain_merge(self):
        n = 10_000
        d = RollbackDSU(n)
        cps = []
        for i in range(n - 1):            # 链式合并 n-1 次
            cps.append(d.checkpoint())
            d.union(i, i + 1)
        self.assertEqual(d.component_size(0), n)
        # 树高必须被按大小合并限制在 log2(n) 以内（无路径压缩）
        height = 0
        for i in range(n):
            h, x = 0, i
            while d._parent[x] != x:
                x = d._parent[x]
                h += 1
            height = max(height, h)
        self.assertLessEqual(height, (n - 1).bit_length())
        # 逐级回滚，组件数逐一增加
        for i in range(n - 2, -1, -1):
            d.rollback(cps[i])
            self.assertEqual(d.num_components, n - i)
        self.assertEqual(d.partition(), {i: [i] for i in range(n)})

    def test_invalid_checkpoint_raises(self):
        d = RollbackDSU(3)
        d.union(0, 1)
        with self.assertRaises(ValueError):
            d.rollback(2)                 # 超过当前日志深度
        with self.assertRaises(ValueError):
            d.rollback(-1)

    def test_rollback_does_not_touch_earlier_state(self):
        d = RollbackDSU(10)
        d.union(0, 1); d.union(2, 3); d.union(0, 2)
        cp = d.checkpoint()
        snapshot = d.partition()
        d.union(4, 5); d.union(6, 7); d.union(4, 6); d.union(0, 4)
        d.rollback(cp)
        self.assertEqual(d.partition(), snapshot)   # 检查点之前的状态完全保留
        self.assertEqual(d.component_size(0), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
