"""reheal 库的自测：覆盖一致性、损坏、分歧、并发与幂等。"""

import os
import shutil
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import reheal

CHUNK = 64  # 测试用小chunk，便于构造场景


def make_chunk(seed: int, size: int = CHUNK) -> bytes:
    return bytes((seed * 31 + i) % 256 for i in range(size))


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="reheal-test-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def replica(self, name: str, chunks: list[bytes]) -> str:
        path = os.path.join(self.dir, name)
        with open(path, "wb") as fh:
            for data in chunks:
                fh.write(data)
        return path

    def read_all(self, path: str) -> bytes:
        with open(path, "rb") as fh:
            return fh.read()


class TestScan(Base):
    def test_all_replicas_identical(self):
        data = [make_chunk(i) for i in range(5)]
        paths = [self.replica(f"r{i}.bin", data) for i in range(3)]
        report = reheal.scan_replicas(paths, CHUNK)
        self.assertTrue(report.consistent)
        self.assertEqual(report.divergent, [])
        for diff in report.replicas:
            self.assertTrue(diff.clean())

    def test_single_replica_corrupted(self):
        good = [make_chunk(i) for i in range(4)]
        bad = list(good)
        bad[1] = make_chunk(999)
        bad[3] = make_chunk(998)
        paths = [self.replica("r0.bin", good),
                 self.replica("r1.bin", bad),
                 self.replica("r2.bin", good)]
        report = reheal.scan_replicas(paths, CHUNK)
        self.assertFalse(report.consistent)
        self.assertEqual(report.replicas[1].mismatched, [1, 3])
        self.assertTrue(report.replicas[0].clean())
        self.assertTrue(report.replicas[2].clean())
        self.assertEqual(report.divergent, [])

    def test_missing_and_extra_chunks(self):
        full = [make_chunk(i) for i in range(4)]
        short = full[:2]                      # 缺块
        long_ = full + [make_chunk(777)]      # 多块
        paths = [self.replica("r0.bin", full),
                 self.replica("r1.bin", short),
                 self.replica("r2.bin", full)]
        report = reheal.scan_replicas(paths, CHUNK)
        self.assertEqual(report.replicas[1].missing, [2, 3])

        paths2 = [self.replica("a.bin", full),
                  self.replica("b.bin", long_),
                  self.replica("c.bin", full)]
        report2 = reheal.scan_replicas(paths2, CHUNK)
        self.assertEqual(report2.replicas[1].extra, [4])

    def test_checksum_failed_chunk(self):
        data = [make_chunk(i) for i in range(3)]
        paths = [self.replica(f"r{i}.bin", data) for i in range(3)]
        # r0 的块 1 被静默损坏，但清单仍记录正确哈希 -> 校验失败
        reheal.write_chunk(paths[0], 1, make_chunk(555), CHUNK)
        for p in paths:
            reheal.build_manifest(p, CHUNK)
        # 修正 r1/r2 的清单为正确内容（r0 的清单是损坏后生成的，重建成正确的）
        good_manifest = {i: reheal._sha256(c) for i, c in enumerate(data)}
        reheal.save_manifest(paths[0], good_manifest)

        report = reheal.scan_replicas(paths, CHUNK)
        self.assertEqual(report.replicas[0].checksum_failed, [1])
        # 校验失败块弃权，但其余两副本一致 -> 仍能形成多数派
        self.assertEqual(report.divergent, [])

    def test_three_way_conflict_is_divergent(self):
        paths = [self.replica(f"r{i}.bin", [make_chunk(100 + i)]) for i in range(3)]
        report = reheal.scan_replicas(paths, CHUNK)
        self.assertEqual(report.divergent, [0])
        self.assertFalse(report.consistent)

    def test_two_replicas_disagree_is_divergent(self):
        paths = [self.replica("r0.bin", [make_chunk(1)]),
                 self.replica("r1.bin", [make_chunk(2)])]
        report = reheal.scan_replicas(paths, CHUNK)
        self.assertEqual(report.divergent, [0])


class TestRepair(Base):
    def test_repair_single_corrupted_replica(self):
        good = [make_chunk(i) for i in range(4)]
        bad = list(good)
        bad[1] = make_chunk(999)
        bad[2] = make_chunk(998)
        paths = [self.replica("r0.bin", good),
                 self.replica("r1.bin", bad),
                 self.replica("r2.bin", good)]
        report = reheal.repair_replicas(paths, CHUNK)
        self.assertFalse(report.refused)
        self.assertEqual(report.applied[paths[1]], [1, 2])
        for p in paths:
            self.assertEqual(self.read_all(p), b"".join(good))
        self.assertTrue(reheal.scan_replicas(paths, CHUNK).consistent)

    def test_repair_missing_and_truncate_extra(self):
        full = [make_chunk(i) for i in range(3)]
        p0 = self.replica("r0.bin", full)
        p1 = self.replica("r1.bin", full[:1])                 # 缺块
        p2 = self.replica("r2.bin", full + [make_chunk(9)])   # 多块
        report = reheal.repair_replicas([p0, p1, p2], CHUNK)
        self.assertEqual(report.applied[p1], [1, 2])
        self.assertEqual(report.truncated[p2], 3 * CHUNK)
        for p in (p0, p1, p2):
            self.assertEqual(self.read_all(p), b"".join(full))

    def test_repair_refused_on_divergence(self):
        before = [make_chunk(100 + i) for i in range(3)]
        paths = [self.replica(f"r{i}.bin", [before[i]]) for i in range(3)]
        report = reheal.repair_replicas(paths, CHUNK)
        self.assertTrue(report.refused)
        self.assertEqual(report.divergent, [0])
        self.assertEqual(report.applied, {})
        # 文件保持原样，绝不动分歧块
        for i, p in enumerate(paths):
            self.assertEqual(self.read_all(p), before[i])

    def test_repair_refused_with_two_replicas(self):
        p0 = self.replica("r0.bin", [make_chunk(1)])
        p1 = self.replica("r1.bin", [make_chunk(2)])
        report = reheal.repair_replicas([p0, p1], CHUNK)
        self.assertTrue(report.refused)
        self.assertEqual(report.applied, {})
        self.assertEqual(self.read_all(p0), make_chunk(1))
        self.assertEqual(self.read_all(p1), make_chunk(2))

    def test_repair_checksum_failed_chunk_and_manifest(self):
        good = [make_chunk(i) for i in range(3)]
        paths = [self.replica(f"r{i}.bin", good) for i in range(3)]
        for p in paths:
            reheal.build_manifest(p, CHUNK)
        # r0 块 1 被静默损坏（清单仍是正确哈希）
        reheal.write_chunk(paths[0], 1, make_chunk(555), CHUNK)

        report = reheal.repair_replicas(paths, CHUNK)
        self.assertEqual(report.applied[paths[0]], [1])
        self.assertEqual(self.read_all(paths[0]), b"".join(good))
        # 修复后再次扫描：无校验失败、无差异
        self.assertTrue(reheal.scan_replicas(paths, CHUNK).consistent)

    def test_repair_is_idempotent(self):
        good = [make_chunk(i) for i in range(5)]
        bad = list(good)
        bad[0] = make_chunk(900)
        bad[4] = make_chunk(901)
        paths = [self.replica("r0.bin", good),
                 self.replica("r1.bin", bad),
                 self.replica("r2.bin", good)]
        first = reheal.repair_replicas(paths, CHUNK)
        self.assertEqual(first.applied[paths[1]], [0, 4])
        snapshot = {p: self.read_all(p) for p in paths}

        second = reheal.repair_replicas(paths, CHUNK)
        # 断言：第二次修复不产生任何额外改动
        self.assertEqual(second.applied, {})
        self.assertEqual(second.truncated, {})
        self.assertEqual(second.manifests_updated, {})
        self.assertEqual(second.skipped_concurrent, {})
        for p in paths:
            self.assertEqual(self.read_all(p), snapshot[p])


class TestConcurrency(Base):
    def test_concurrent_writes_not_rolled_back_and_repair_holds(self):
        n_chunks = 48
        good = [make_chunk(i) for i in range(n_chunks)]
        bad = list(good)
        corrupted = list(range(2, 8))
        for c in corrupted:
            bad[c] = make_chunk(900 + c)
        paths = [self.replica("r0.bin", bad),
                 self.replica("r1.bin", good),
                 self.replica("r2.bin", good)]

        # 写入线程：在修复期间持续向另一区段（块 20..29）写新数据
        stop = threading.Event()
        write_errors = []

        def writer():
            counter = 0
            try:
                while not stop.is_set():
                    for c in range(20, 30):
                        value = make_chunk(5000 + counter, CHUNK)
                        for p in paths:
                            reheal.write_chunk(p, c, value, CHUNK)
                        counter += 1
            except Exception as exc:  # pragma: no cover
                write_errors.append(exc)

        thread = threading.Thread(target=writer)
        thread.start()
        try:
            report = reheal.repair_replicas(paths, CHUNK)
        finally:
            stop.set()
            thread.join()

        self.assertEqual(write_errors, [])
        # 已修复的块保持多数派内容，未被并发写入回退
        for c in corrupted:
            for p in paths:
                self.assertEqual(reheal.read_chunk(p, c, CHUNK), good[c],
                                 f"chunk {c} of {p} rolled back")
        # 并发写入的块在三副本间保持一致（修复器没有插手/破坏）
        for c in range(20, 30):
            values = {reheal.read_chunk(p, c, CHUNK) for p in paths}
            self.assertEqual(len(values), 1, f"chunk {c} inconsistent")
        self.assertEqual(report.skipped_concurrent, {})

    def test_repair_does_not_clobber_concurrent_write_to_bad_chunk(self):
        good = [make_chunk(i) for i in range(4)]
        bad = list(good)
        bad[1] = make_chunk(999)
        paths = [self.replica("r0.bin", bad),
                 self.replica("r1.bin", good),
                 self.replica("r2.bin", good)]

        scan = reheal.scan_replicas(paths, CHUNK)
        # 扫描之后、修复之前：业务线程把新数据写进了待修复的块
        new_value = make_chunk(4242)
        reheal.write_chunk(paths[0], 1, new_value, CHUNK)

        report = reheal.repair_replicas(paths, CHUNK, scan=scan)
        # CAS 检测到并发修改：跳过该块，绝不用旧的多数派内容回退新写入
        self.assertEqual(report.skipped_concurrent.get(paths[0]), [1])
        self.assertEqual(reheal.read_chunk(paths[0], 1, CHUNK), new_value)


if __name__ == "__main__":
    unittest.main(verbosity=2)
