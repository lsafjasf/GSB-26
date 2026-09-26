"""自测：边界情形单元测试 + 与全量校验的对拍测试。

运行：python3 -m unittest test_incremental_checksum -v
"""

import random
import unittest

from incremental_checksum import (
    MISMATCH, PASS, UNCOVERED,
    ShardStore, ShardVerifier, _digest,
)


def full_verify_baseline(verifier: ShardVerifier):
    """独立实现的全量校验（对拍基准）：逐分片重算并与基准摘要比对。

    基准选择规则与增量校验一致：
    - 变化分片（版本不同或新增）对写入时摘要；
    - 未变化分片对清单摘要；
    - 清单中多余的分片（数据缩短）视为不一致。
    """
    store, manifest = verifier.store, verifier.manifest
    mismatches = []
    for i in range(len(store)):
        rec = manifest.get(i)
        digest = _digest(store.read_shard(i), store.hash_name)
        if rec is None or rec.version != store.versions[i]:
            if digest != store.write_digest(i):
                mismatches.append(i)
        elif digest != rec.digest:
            mismatches.append(i)
    for i in manifest:
        if i >= len(store):
            mismatches.append(i)
    return MISMATCH if mismatches else PASS, sorted(mismatches)


class EdgeCaseTests(unittest.TestCase):
    def test_empty_data(self):
        store = ShardStore.from_bytes(b"", shard_size=16)
        v = ShardVerifier(store)
        v.commit()
        report = v.verify(sample_ratio=0.5, seed=1)
        self.assertEqual(report.total_shards, 0)
        self.assertEqual(report.conclusion, PASS)  # 无分片即无未覆盖
        self.assertEqual(report.coverage, 1.0)

    def test_single_shard(self):
        store = ShardStore.from_bytes(b"hello", shard_size=16)
        v = ShardVerifier(store)
        v.commit()
        self.assertEqual(v.verify(sample_ratio=0.0, seed=1).conclusion, UNCOVERED)
        self.assertEqual(v.verify(sample_ratio=1.0, seed=1).conclusion, PASS)
        store.tamper_shard(0, b"jello")
        report = v.verify(sample_ratio=1.0, seed=1)
        self.assertEqual(report.conclusion, MISMATCH)
        self.assertEqual(report.mismatches, [0])

    def test_all_shards_changed(self):
        store = ShardStore.from_bytes(bytes(range(64)), shard_size=8)
        v = ShardVerifier(store)
        v.commit()
        for i in range(len(store)):
            store.write_shard(i, bytes([255 - i] * 8))
        report = v.verify(sample_ratio=0.0, seed=1)  # 抽样比例为 0 也应全查
        self.assertEqual(report.changed_shards, 8)
        self.assertEqual(report.checked_shards, 8)
        self.assertEqual(report.conclusion, PASS)  # 合法写入，全部一致

    def test_multi_point_tamper_within_one_shard(self):
        # 分片内多处篡改：篡改后的分片只要被抽中/检查就必须被发现
        store = ShardStore.from_bytes(b"A" * 64, shard_size=16)
        v = ShardVerifier(store)
        v.commit()
        data = bytearray(store.read_shard(2))
        data[0] = ord("B"); data[7] = ord("C"); data[15] = ord("D")
        store.tamper_shard(2, bytes(data))
        report = v.verify(sample_ratio=1.0, seed=1)
        self.assertEqual(report.conclusion, MISMATCH)
        self.assertEqual(report.mismatches, [2])

    def test_uncovered_never_counts_as_pass(self):
        store = ShardStore.from_bytes(bytes(100), shard_size=10)
        v = ShardVerifier(store)
        v.commit()
        report = v.verify(sample_ratio=0.3, seed=42)
        self.assertEqual(report.conclusion, UNCOVERED)
        self.assertGreater(report.uncovered_shards, 0)
        self.assertAlmostEqual(
            report.coverage,
            report.checked_shards / report.total_shards)

    def test_changed_shard_forced_recheck_even_at_zero_ratio(self):
        store = ShardStore.from_bytes(bytes(50), shard_size=10)
        v = ShardVerifier(store)
        v.commit()
        store.write_shard(3, b"x" * 10)
        report = v.verify(sample_ratio=0.0, seed=1)
        self.assertEqual(report.changed_shards, 1)
        self.assertEqual(report.checked_shards, 1)
        self.assertEqual(report.conclusion, UNCOVERED)  # 其余 9 片未覆盖

    def test_shrink_detected(self):
        store = ShardStore.from_bytes(bytes(50), shard_size=10)
        v = ShardVerifier(store)
        v.commit()
        store.truncate(3)
        report = v.verify(sample_ratio=1.0, seed=1)
        self.assertEqual(report.conclusion, MISMATCH)
        self.assertEqual(report.mismatches, [3, 4])

    def test_growth_treated_as_changed(self):
        store = ShardStore.from_bytes(bytes(20), shard_size=10)
        v = ShardVerifier(store)
        v.commit()
        store.append_shard(b"z" * 10)
        report = v.verify(sample_ratio=0.0, seed=1)
        self.assertEqual(report.changed_shards, 1)
        self.assertEqual(report.conclusion, UNCOVERED)

    def test_invalid_ratio(self):
        v = ShardVerifier(ShardStore.from_bytes(b"abc", shard_size=2))
        with self.assertRaises(ValueError):
            v.verify(sample_ratio=1.5)


class DifferentialTests(unittest.TestCase):
    """对拍：随机操作序列下，sample_ratio=1.0 的增量校验结论
    必须与独立实现的全量校验完全一致。"""

    def test_against_full_verify(self):
        rng = random.Random(20260926)
        for trial in range(300):
            size = rng.randrange(0, 200)
            shard_size = rng.choice([1, 3, 7, 16])
            store = ShardStore.from_bytes(
                rng.randbytes(size), shard_size=shard_size)
            v = ShardVerifier(store)
            v.commit()
            # 随机操作：合法写 / 旁路篡改 / 追加 / 截断
            for _ in range(rng.randrange(0, 6)):
                op = rng.choice(["write", "tamper", "append", "truncate"])
                if op in ("write", "tamper") and len(store) == 0:
                    continue
                if op == "write":
                    i = rng.randrange(len(store))
                    store.write_shard(i, rng.randbytes(shard_size))
                elif op == "tamper":
                    i = rng.randrange(len(store))
                    store.tamper_shard(i, rng.randbytes(shard_size))
                elif op == "append":
                    store.append_shard(rng.randbytes(rng.randrange(1, shard_size + 1)))
                elif len(store) > 0:
                    store.truncate(rng.randrange(len(store)))
            report = v.verify(sample_ratio=1.0, seed=rng.randrange(1 << 30))
            expected_conclusion, expected_mismatches = full_verify_baseline(v)
            self.assertEqual(report.conclusion, expected_conclusion,
                             f"trial={trial} mismatches={report.mismatches}")
            self.assertEqual(report.mismatches, expected_mismatches,
                             f"trial={trial}")
            # ratio=1.0 时必须全覆盖：结论只能是 PASS 或 MISMATCH
            self.assertIn(report.conclusion, (PASS, MISMATCH))
            self.assertEqual(report.uncovered_shards, 0)


if __name__ == "__main__":
    unittest.main()
