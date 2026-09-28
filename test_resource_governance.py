"""资源治理迭代测试：多轮 k 路归并、临时目录降级、异常清理、对拍。

覆盖：
  - 多轮归并的守恒/稳定/有序，归并路数与轮次元信息；
  - 临时目录不可写（真实只读目录 + 注入失败）：
      on_temp_error="memory" -> 内存降级，backend=="memory"，结果对拍一致；
      on_temp_error="fail"   -> TempDirUnavailableError，且无残留；
  - 写 run 文件中途失败（flush 阶段）同样触发降级；
  - 多轮归并的【中间轮】与【最终轮】中途抛异常，均不残留任何临时文件；
  - 降级路径与正常磁盘路径结果逐字节一致（多组数据 × 多组 chunk/ways 对拍）。

运行：python3 -m unittest test_resource_governance -v
"""

import os
import random
import tempfile
import unittest

import external_sort
from external_sort import (
    external_sort,
    TempDirUnavailableError,
    POLICY_MEMORY,
    POLICY_FAIL,
)


def make_key(line):
    return int(line.split("\t", 1)[0])


def write_records(path, records):
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(records)


def read_records(path):
    with open(path, encoding="utf-8") as f:
        return f.readlines()


def gen_records(n, distinct_keys, seed):
    rng = random.Random(seed)
    return ["%d\tr%06d\n" % (rng.randrange(distinct_keys), i) for i in range(n)]


def temp_entries(directory):
    return set(os.listdir(directory))


class MultiWayMergeTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = self._td.name
        self.src = os.path.join(self.dir, "input.txt")
        self.dst = os.path.join(self.dir, "output.txt")

    def tearDown(self):
        self._td.cleanup()

    def _check(self, records, chunk_size, merge_ways, expect_rounds):
        write_records(self.src, records)
        result = external_sort(self.src, self.dst, make_key,
                               chunk_size=chunk_size, temp_dir=self.dir,
                               merge_ways=merge_ways)
        out = read_records(self.dst)
        self.assertEqual(len(out), len(records))                       # 守恒
        self.assertEqual(out, sorted(records, key=make_key))          # 稳定+有序
        self.assertEqual(result.backend, "disk")
        self.assertEqual(result.num_runs, -(-len(records) // chunk_size))
        self.assertEqual(result.merge_rounds, expect_rounds)
        self.assertEqual(result.merge_ways, merge_ways)
        return result

    def test_two_way_multi_round(self):
        # 30 条 / chunk=10 -> 3 runs / ways=2：r1 (2+1直通)->2，最终轮 ->1，共 2 轮
        self._check(gen_records(30, 7, 1), 10, 2, expect_rounds=2)

    def test_three_way_multi_round_with_passthrough(self):
        # 120 条 / chunk=10 -> 12 runs / ways=3：
        # r1 12->4，r2 (3+1直通)->2，最终轮 ->1，共 3 轮
        self._check(gen_records(120, 7, 2), 10, 3, expect_rounds=3)

    def test_all_same_key_stable_across_rounds(self):
        # 全部键相同 + 多轮归并：稳定排序要求输出与输入逐字节相同
        records = ["5\tp%06d\n" % i for i in range(200)]
        write_records(self.src, records)
        external_sort(self.src, self.dst, make_key, chunk_size=7,
                      temp_dir=self.dir, merge_ways=3)
        self.assertEqual(read_records(self.dst), records)

    def test_empty_input_multi_round(self):
        write_records(self.src, [])
        result = external_sort(self.src, self.dst, make_key, chunk_size=4,
                               temp_dir=self.dir, merge_ways=2)
        self.assertEqual(read_records(self.dst), [])
        self.assertEqual(result.num_runs, 0)
        self.assertEqual(result.merge_rounds, 0)

    def test_invalid_merge_ways(self):
        write_records(self.src, ["1\ta\n"])
        with self.assertRaises(ValueError):
            external_sort(self.src, self.dst, make_key, merge_ways=1)

    def test_no_residue_after_multi_round_success(self):
        records = gen_records(300, 13, 3)
        self._check(records, 7, 2, expect_rounds=6)
        leftovers = [p for p in os.listdir(self.dir)
                     if p.endswith(".run") or p.startswith("extsort_")]
        self.assertEqual(leftovers, [])


class DegradationTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = self._td.name
        self.src = os.path.join(self.dir, "input.txt")
        self.dst = os.path.join(self.dir, "output.txt")
        self.records = gen_records(2000, 50, seed=11)
        write_records(self.src, self.records)

    def tearDown(self):
        self._td.cleanup()

    def _readonly_dir(self):
        ro_dir = os.path.join(self.dir, "readonly")
        os.mkdir(ro_dir)
        os.chmod(ro_dir, 0o555)
        return ro_dir

    def _assert_result_matches_reference(self, dst, result, backend):
        self.assertEqual(result.backend, backend)
        self.assertEqual(read_records(dst),
                         sorted(self.records, key=make_key))

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                     "root 会绕过文件权限，无法复现只读目录（另有注入测试）")
    def test_readonly_dir_falls_back_to_memory(self):
        ro_dir = self._readonly_dir()
        try:
            before = temp_entries(self.dir)
            result = external_sort(self.src, self.dst, make_key,
                                   chunk_size=64, temp_dir=ro_dir,
                                   merge_ways=4,
                                   on_temp_error=POLICY_MEMORY)
            self._assert_result_matches_reference(self.dst, result, "memory")
            # 只读目录里无任何写入痕迹，临时目录也无残留
            self.assertEqual(os.listdir(ro_dir), [])
            self.assertEqual(temp_entries(self.dir) - before,
                             {os.path.basename(self.dst)})
        finally:
            os.chmod(ro_dir, 0o755)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                     "root 会绕过文件权限，无法复现只读目录（另有注入测试）")
    def test_readonly_dir_fail_policy_raises_without_residue(self):
        ro_dir = self._readonly_dir()
        try:
            with self.assertRaises(TempDirUnavailableError):
                external_sort(self.src, self.dst, make_key,
                              chunk_size=64, temp_dir=ro_dir,
                              on_temp_error=POLICY_FAIL)
            self.assertEqual(os.listdir(ro_dir), [])
            self.assertFalse(os.path.exists(self.dst))
        finally:
            os.chmod(ro_dir, 0o755)

    def test_mkdtemp_failure_falls_back_to_memory(self):
        # 注入 TemporaryDirectory 失败：root 环境下也能稳定复现降级路径。
        original_init = tempfile.TemporaryDirectory.__init__

        def boom_init(self_inner, *a, **kw):
            raise PermissionError("injected: temp dir denied")

        tempfile.TemporaryDirectory.__init__ = boom_init
        try:
            result = external_sort(self.src, self.dst, make_key,
                                   chunk_size=64, temp_dir=self.dir,
                                   on_temp_error=POLICY_MEMORY)
            self._assert_result_matches_reference(self.dst, result, "memory")
        finally:
            tempfile.TemporaryDirectory.__init__ = original_init
        leftovers = [p for p in os.listdir(self.dir)
                     if p.endswith(".run") or p.startswith("extsort_")]
        self.assertEqual(leftovers, [])

    def test_mkdtemp_failure_fail_policy_raises(self):
        original_init = tempfile.TemporaryDirectory.__init__

        def boom_init(self_inner, *a, **kw):
            raise PermissionError("injected: temp dir denied")

        tempfile.TemporaryDirectory.__init__ = boom_init
        try:
            with self.assertRaises(TempDirUnavailableError):
                external_sort(self.src, self.dst, make_key,
                              chunk_size=64, temp_dir=self.dir,
                              on_temp_error=POLICY_FAIL)
        finally:
            tempfile.TemporaryDirectory.__init__ = original_init
        self.assertIsInstance(TempDirUnavailableError("x"), OSError)
        self.assertFalse(os.path.exists(self.dst))

    def test_run_write_failure_falls_back_to_memory(self):
        # 首个 run 文件 flush 时写入失败（临时目录中途变只读/配额耗尽的等价注入）。
        import builtins
        real_open = builtins.open
        attempts = {"n": 0}

        def flaky_open(path, *a, **kw):
            p = str(path)
            mode = a[0] if a else kw.get("mode", "r")
            if p.startswith(os.path.join(self.dir, "extsort_")) \
                    and p.endswith(".run") and "w" in mode:
                attempts["n"] += 1
                if attempts["n"] == 1:
                    raise OSError("injected: run flush failed")
            return real_open(path, *a, **kw)

        builtins.open = flaky_open
        try:
            result = external_sort(self.src, self.dst, make_key,
                                   chunk_size=64, temp_dir=self.dir,
                                   on_temp_error=POLICY_MEMORY)
            self._assert_result_matches_reference(self.dst, result, "memory")
        finally:
            builtins.open = real_open
        leftovers = [p for p in os.listdir(self.dir)
                     if p.endswith(".run") or p.startswith("extsort_")]
        self.assertEqual(leftovers, [])

    def test_output_unwritable_is_not_misclassified(self):
        # 最终输出不可写必须原样抛 OSError（且若误走内存重排，错误依然上抛），
        # 不能静默成功或误报为临时目录问题。
        bad_dst = os.path.join(self.dir, "no_such_dir", "out.txt")
        with self.assertRaises(OSError):
            external_sort(self.src, bad_dst, make_key, chunk_size=64,
                          temp_dir=self.dir, on_temp_error=POLICY_MEMORY)


class MergeExceptionCleanupTests(unittest.TestCase):
    """多轮归并中途异常的清理（要求在中间轮与最终轮都生效）。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = self._td.name
        self.src = os.path.join(self.dir, "input.txt")
        self.dst = os.path.join(self.dir, "output.txt")

    def tearDown(self):
        self._td.cleanup()

    def _run_with_boom(self, explode_at):
        # n=120, chunk=10 -> 12 runs, ways=3 -> 3 轮：
        #   split 调用 key 120 次；
        #   r1（12->4）归并全部 120 条 -> 120 次（累计 240）；
        #   r2（3+1直通 ->2）只归并 90 条 -> 90 次（累计 330，为中间轮）；
        #   最终轮（2->output）归并 120 次（累计 450）。
        n, chunk, ways = 120, 10, 3
        records = gen_records(n, 7, seed=5)
        write_records(self.src, records)
        calls = {"n": 0}

        def boom_key(line):
            calls["n"] += 1
            if calls["n"] == explode_at:
                raise RuntimeError("simulated merge failure at %d" % explode_at)
            return make_key(line)

        before = temp_entries(self.dir)
        with self.assertRaises(RuntimeError):
            external_sort(self.src, self.dst, boom_key, chunk_size=chunk,
                          temp_dir=self.dir, merge_ways=ways)
        # 任何异常路径都不残留临时目录/run 文件（dst 半成品允许存在）。
        new_entries = temp_entries(self.dir) - before \
            - {os.path.basename(self.dst)}
        self.assertEqual(new_entries, set())

    def test_exception_in_intermediate_round_cleans_up(self):
        self._run_with_boom(244)  # 落在 r2（中间轮）

    def test_exception_in_final_round_cleans_up(self):
        self._run_with_boom(333)  # 落在最终轮（写 output 期间）


class DifferentialFallbackTests(unittest.TestCase):
    """降级（内存）路径与正常磁盘路径结果逐字节对拍。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = self._td.name
        self.src = os.path.join(self.dir, "input.txt")
        self.disk_dst = os.path.join(self.dir, "disk.txt")
        self.mem_dst = os.path.join(self.dir, "mem.txt")

    def tearDown(self):
        self._td.cleanup()

    def _force_memory(self):
        original_init = tempfile.TemporaryDirectory.__init__

        def boom_init(self_inner, *a, **kw):
            raise PermissionError("injected: temp dir denied")

        tempfile.TemporaryDirectory.__init__ = boom_init
        return original_init

    def _datasets(self):
        return [
            gen_records(1, 3, 0),
            gen_records(50, 50, 1),          # 键全部不同
            gen_records(2000, 10, 2),        # 大量重复键
            ["5\tdup\n"] * 500,              # 完全相同的记录
            [],                              # 空输入
        ]

    def test_disk_vs_memory_identical(self):
        original_init = None
        for records in self._datasets():
            for chunk_size, merge_ways in [(1, 2), (37, 3), (1000, 8)]:
                write_records(self.src, records)
                disk_result = external_sort(
                    self.src, self.disk_dst, make_key,
                    chunk_size=chunk_size, temp_dir=self.dir,
                    merge_ways=merge_ways, on_temp_error=POLICY_FAIL)
                self.assertEqual(disk_result.backend, "disk")
                original_init = self._force_memory()
                try:
                    mem_result = external_sort(
                        self.src, self.mem_dst, make_key,
                        chunk_size=chunk_size, temp_dir=self.dir,
                        merge_ways=merge_ways, on_temp_error=POLICY_MEMORY)
                finally:
                    tempfile.TemporaryDirectory.__init__ = original_init
                self.assertEqual(mem_result.backend, "memory")
                # 逐字节对拍（含换行与相同键相对顺序）
                with open(self.disk_dst, "rb") as a, \
                        open(self.mem_dst, "rb") as b:
                    self.assertEqual(a.read(), b.read())


if __name__ == "__main__":
    unittest.main()
