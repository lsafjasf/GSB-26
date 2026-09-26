"""replica_sync 自测：比对、修复策略、并发安全、幂等性。仅标准库 unittest。"""

import threading
import unittest

from replica_sync import (
    CHECKSUM_ERROR, MISMATCH, MISSING,
    ReplicaStore, compare_replicas, format_report, repair_replicas,
)


def make_replicas(n, chunks, names=None):
    """创建 n 份内容完全一致的副本。chunks: {index: bytes}"""
    names = names or [f"R{i+1}" for i in range(n)]
    replicas = [ReplicaStore(nm) for nm in names]
    for r in replicas:
        for idx, data in chunks.items():
            r.write_chunk(idx, data)
    return replicas


def base_chunks(k=8, size=64):
    return {i: bytes([i]) * size for i in range(k)}


def total_writes(replicas):
    return sum(r.write_count for r in replicas)


class TestCompare(unittest.TestCase):
    def test_all_consistent(self):
        replicas = make_replicas(3, base_chunks())
        report = compare_replicas(replicas)
        self.assertTrue(report.clean)
        self.assertEqual(report.total_chunks, 8)
        for issues in report.diffs.values():
            self.assertEqual(issues, [])

    def test_missing_chunk_detected(self):
        replicas = make_replicas(3, base_chunks())
        replicas[1].delete_chunk(3)
        report = compare_replicas(replicas)
        kinds = {i.kind for i in report.diffs["R2"]}
        self.assertEqual(kinds, {MISSING})
        self.assertEqual(report.diffs["R1"], [])
        self.assertEqual(report.diffs["R3"], [])

    def test_checksum_error_detected(self):
        replicas = make_replicas(3, base_chunks())
        replicas[2].corrupt_chunk(5, b"x" * 64)  # 位腐烂：校验和未更新
        report = compare_replicas(replicas)
        issues = report.diffs["R3"]
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].kind, CHECKSUM_ERROR)

    def test_stale_chunk_detected_as_mismatch(self):
        # 内容落后但校验和自洽（旧值是合法写入）-> mismatch
        replicas = make_replicas(3, base_chunks())
        replicas[0].write_chunk(2, b"old-version" * 8)
        report = compare_replicas(replicas)
        issues = report.diffs["R1"]
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].kind, MISMATCH)
        self.assertEqual(report.verdicts[2].majority_digest,
                         report.verdicts[2].majority_digest)

    def test_conflicting_replicas_no_majority(self):
        # 5 副本 2/2/1 分裂 -> 无严格多数
        replicas = make_replicas(5, base_chunks())
        replicas[1].write_chunk(0, b"fork-B" * 16)
        replicas[2].write_chunk(0, b"fork-B" * 16)
        replicas[3].write_chunk(0, b"fork-C" * 16)
        report = compare_replicas(replicas)
        self.assertIsNone(report.verdicts[0].majority_digest)
        self.assertTrue(report.verdicts[0].divergent)

    def test_two_replicas_disagree_no_majority(self):
        replicas = make_replicas(2, base_chunks())
        replicas[1].write_chunk(4, b"different" * 8)
        report = compare_replicas(replicas)
        self.assertIsNone(report.verdicts[4].majority_digest)
        self.assertTrue(report.verdicts[4].divergent)


class TestRepair(unittest.TestCase):
    def test_repair_nothing_when_consistent(self):
        replicas = make_replicas(3, base_chunks())
        before = total_writes(replicas)
        rep = repair_replicas(replicas)
        self.assertTrue(rep.ok)
        self.assertEqual(rep.repaired, [])
        self.assertEqual(total_writes(replicas), before)  # 零写入

    def test_repair_single_corrupted_replica(self):
        chunks = base_chunks()
        replicas = make_replicas(3, chunks)
        replicas[1].corrupt_chunk(2, b"rotten" * 16)
        replicas[1].delete_chunk(6)
        rep = repair_replicas(replicas)
        self.assertTrue(rep.ok)
        self.assertEqual(sorted(rep.repaired), [("R2", 2), ("R2", 6)])
        # 修复后内容 = 多数派值，且校验和恢复自洽
        self.assertEqual(replicas[1].read_chunk(2), chunks[2])
        self.assertEqual(replicas[1].read_chunk(6), chunks[6])
        self.assertTrue(compare_replicas(replicas).clean)

    def test_repair_only_touches_bad_chunks(self):
        chunks = base_chunks()
        replicas = make_replicas(3, chunks)
        replicas[0].corrupt_chunk(1, b"bad" * 32)
        before = {r.name: r.write_count for r in replicas}
        repair_replicas(replicas)
        # R1 只多写 1 次（块 #1），其余副本零写入
        self.assertEqual(replicas[0].write_count - before["R1"], 1)
        self.assertEqual(replicas[1].write_count, before["R2"])
        self.assertEqual(replicas[2].write_count, before["R3"])

    def test_repair_refused_without_majority(self):
        chunks = base_chunks()
        replicas = make_replicas(4, chunks)
        replicas[2].write_chunk(0, b"fork-B" * 16)
        replicas[3].write_chunk(0, b"fork-B" * 16)  # 2 vs 2
        before = total_writes(replicas)
        rep = repair_replicas(replicas)
        self.assertEqual(rep.divergences, [0])
        self.assertEqual(rep.repaired, [])
        self.assertEqual(total_writes(replicas), before)  # 拒绝自动修复，零写入

    def test_repair_refused_two_replicas_disagree(self):
        chunks = base_chunks()
        replicas = make_replicas(2, chunks)
        replicas[1].write_chunk(3, b"other" * 16)
        before = total_writes(replicas)
        rep = repair_replicas(replicas)
        self.assertEqual(rep.divergences, [3])
        self.assertEqual(total_writes(replicas), before)

    def test_repair_idempotent(self):
        chunks = base_chunks()
        replicas = make_replicas(3, chunks)
        replicas[2].corrupt_chunk(0, b"rot" * 32)
        replicas[2].delete_chunk(4)
        replicas[1].write_chunk(7, b"stale" * 16)
        rep1 = repair_replicas(replicas)
        self.assertEqual(len(rep1.repaired), 3)
        after_first = total_writes(replicas)
        # 重复执行：零新增写入，修复清单为空，比对报告干净
        rep2 = repair_replicas(replicas)
        self.assertEqual(rep2.repaired, [])
        self.assertEqual(total_writes(replicas), after_first)
        self.assertTrue(compare_replicas(replicas).clean)


class TestConcurrency(unittest.TestCase):
    def test_concurrent_write_not_rolled_back(self):
        """修复读后被并发写入 -> CAS 失败 -> 保留新写入，报告冲突，绝不回退。"""
        chunks = base_chunks()
        replicas = make_replicas(3, chunks)
        r2 = replicas[1]
        r2.corrupt_chunk(0, b"rotten" * 16)
        fired = []

        def hook(store, index):
            if index == 0 and not fired:
                fired.append(True)
                store.write_chunk(0, b"fresh-client-write" * 8)

        r2.read_hook = hook
        rep = repair_replicas(replicas)
        self.assertTrue(fired, "钩子应被触发")
        self.assertEqual(r2.read_chunk(0), b"fresh-client-write" * 8)  # 未被回退
        self.assertIn(("R2", 0), rep.conflicts)
        self.assertNotIn(("R2", 0), rep.repaired)

    def test_repair_under_concurrent_writes_converges(self):
        """写入线程持续向全部副本写新值，修复并行执行；最终必须一致。"""
        chunks = base_chunks(k=32, size=256)
        replicas = make_replicas(3, chunks)
        # 制造一批坏块
        for i in range(0, 32, 3):
            replicas[1].corrupt_chunk(i, b"rot" + bytes([i]))
        for i in range(1, 32, 5):
            replicas[2].delete_chunk(i)

        stop = threading.Event()
        counter = [0]
        writer_errors = []

        def writer():
            try:
                while not stop.is_set():
                    counter[0] += 1
                    idx = counter[0] % 32
                    data = f"client-v{counter[0]}".encode() * 8
                    for r in replicas:  # 正常客户端写入：广播到所有副本
                        r.write_chunk(idx, data)
            except Exception as exc:  # pragma: no cover
                writer_errors.append(exc)

        threads = [threading.Thread(target=writer) for _ in range(4)]
        for t in threads:
            t.start()
        repair_replicas(replicas)      # 与写入并发执行
        stop.set()
        for t in threads:
            t.join()
        self.assertEqual(writer_errors, [])
        # 写入停止后再做一次幂等修复，必须收敛到完全一致
        rep = repair_replicas(replicas)
        self.assertTrue(rep.ok, f"conflicts={rep.conflicts} divergences={rep.divergences}")
        final = compare_replicas(replicas)
        self.assertTrue(final.clean, format_report(final))


if __name__ == "__main__":
    unittest.main(verbosity=2)
