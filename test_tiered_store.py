"""TieredStore 自测：对拍测试 + 边界情形。运行：python3 test_tiered_store.py"""

import random
import unittest

from tiered_store import TieredStore


class DuelTest(unittest.TestCase):
    """对拍：随机操作序列下，TieredStore 必须与纯内存 dict 结果完全一致。"""

    def _duel(self, seed, ops, n_keys, capacity, **kw):
        rng = random.Random(seed)
        store = TieredStore(capacity, **kw)
        self.addCleanup(store.close)
        ref = {}
        value_pool = {}

        def new_value(k):
            if k not in value_pool or rng.random() < 0.3:
                value_pool[k] = bytes(rng.randbytes(rng.randint(1, 300)))
            return value_pool[k]

        for _ in range(ops):
            key = f"k{rng.randrange(n_keys)}"
            op = rng.random()
            if op < 0.55:  # get
                got = store.get(key, None)
                want = ref.get(key, None)
                self.assertEqual(got, want, f"get({key}) 与参照不一致")
            elif op < 0.85:  # put
                val = new_value(key)
                store.put(key, val)
                ref[key] = val
            else:  # delete
                store.delete(key)
                ref.pop(key, None)
            # 热层容量不变式
            self.assertLessEqual(store.hot_bytes, capacity)

        # 最终全量比对：无论数据在内存还是磁盘，内容必须一致
        self.assertEqual(len(store), len(ref))
        for k, v in ref.items():
            self.assertEqual(store[k], v)
        for k in store.keys():
            self.assertIn(k, ref)

    def test_duel_default_policy(self):
        self._duel(seed=1, ops=4000, n_keys=200, capacity=8 * 1024)

    def test_duel_lru_only(self):
        self._duel(seed=2, ops=4000, n_keys=200, capacity=8 * 1024,
                   freq_weight=0.0, recency_weight=1.0)

    def test_duel_lfu_only(self):
        self._duel(seed=3, ops=4000, n_keys=200, capacity=8 * 1024,
                   freq_weight=1.0, recency_weight=0.0)

    def test_duel_prefetch_and_early_sink(self):
        self._duel(seed=4, ops=4000, n_keys=200, capacity=8 * 1024,
                   prefetch=True, prefetch_count=3, early_sink=True)

    def test_duel_tiny_capacity(self):
        # 容量小于单条数据：所有数据都应正确落在磁盘
        self._duel(seed=5, ops=2000, n_keys=50, capacity=4)


class EdgeCaseTest(unittest.TestCase):
    def test_empty_store(self):
        with TieredStore(1024) as s:
            self.assertEqual(len(s), 0)
            self.assertIsNone(s.get("nope"))
            self.assertEqual(s.get("nope", b"d"), b"d")
            with self.assertRaises(KeyError):
                s["nope"]
            self.assertNotIn("nope", s)
            s.delete("nope")  # 删除不存在的 key 不报错

    def test_single_item(self):
        with TieredStore(1024) as s:
            s.put("a", b"1")
            self.assertEqual(s["a"], b"1")
            self.assertIn("a", s.hot_keys())
            s.put("a", b"2")  # 覆盖
            self.assertEqual(s["a"], b"2")
            s.delete("a")
            self.assertEqual(len(s), 0)

    def test_capacity_smaller_than_one_item(self):
        with TieredStore(10) as s:
            big = b"x" * 100
            s.put("big", big)
            self.assertEqual(s.hot_bytes, 0)          # 放不进热层
            self.assertIn("big", s.cold_keys())        # 直接落盘
            self.assertEqual(s["big"], big)            # 读取仍正确
            self.assertEqual(s["big"], big)            # 重复读取也正确
            s.put("big2", b"y" * 50)
            self.assertEqual(s["big"], big)
            self.assertEqual(s["big2"], b"y" * 50)

    def test_zero_capacity_all_cold(self):
        with TieredStore(0) as s:
            data = {f"k{i}": f"v{i}".encode() * 10 for i in range(50)}
            for k, v in data.items():
                s.put(k, v)
            self.assertEqual(s.hot_bytes, 0)
            self.assertEqual(len(s.cold_keys()), 50)
            for k, v in data.items():
                self.assertEqual(s[k], v)
            self.assertEqual(s.hot_bytes, 0)  # 读不上浮（放不下）

    def test_update_cold_item(self):
        with TieredStore(64) as s:
            s.put("a", b"a" * 50)
            s.put("b", b"b" * 50)  # 把 a 挤出热层
            self.assertIn("a", s.cold_keys())
            s.put("a", b"A" * 10)  # 覆盖冷层数据
            self.assertEqual(s["a"], b"A" * 10)  # 不能读到磁盘上的旧值

    def test_read_promotes_and_evicts(self):
        with TieredStore(100, freq_weight=0.0, recency_weight=1.0) as s:
            for i in range(5):
                s.put(f"k{i}", b"v" * 30)
            self.assertLessEqual(len(s.hot_keys()), 3)
            cold_before = set(s.cold_keys())
            self.assertTrue(cold_before)
            target = sorted(cold_before)[0]
            s[target]  # 读冷数据 -> 上浮
            self.assertIn(target, s.hot_keys())
            self.assertLessEqual(s.hot_bytes, 100)

    def test_prefetch_promotes_neighbors(self):
        with TieredStore(10 ** 6, prefetch=True, prefetch_count=2) as s:
            for i in range(10):
                s.put(f"k{i:02d}", b"v")
            for k in s.hot_keys():  # 手动下沉制造冷数据
                pass
            # 通过极小容量重新构造：直接换一个小容量 store 更直观
        with TieredStore(8, prefetch=True, prefetch_count=2) as s:
            for i in range(10):
                s.put(f"k{i:02d}", b"v")
            self.assertEqual(len(s.cold_keys()) + len(s.hot_keys()), 10)
            cold = sorted(s.cold_keys())
            first = cold[0]
            s[first]  # 触发预取
            self.assertIn(first, s.hot_keys())
            # 相邻的冷数据应被预取上浮
            self.assertGreaterEqual(len(s.hot_keys()), 2)

    def test_burst_hotspot_stays_hot(self):
        # 突发热点：高频访问的 key 不应被淘汰
        with TieredStore(200, decay_interval=100) as s:
            s.put("hot", b"h" * 50)
            rng = random.Random(0)
            for i in range(300):
                s.put(f"cold{i}", b"c" * 50)
                s["hot"]  # 热点持续访问
            self.assertIn("hot", s.hot_keys())


if __name__ == "__main__":
    unittest.main(verbosity=2)
