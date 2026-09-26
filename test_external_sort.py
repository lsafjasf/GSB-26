"""修复版 external_sort 的性质测试。

断言性质：
  - 守恒：输出条数 == 输入条数；
  - 稳定：相同键保持原始相对顺序（整体输出 == Python 稳定 sorted）；
  - 异常安全：归并中途异常、临时目录只读，均不残留临时文件。

运行：python3 -m unittest test_external_sort -v
"""

import os
import random
import tempfile
import unittest

from external_sort import external_sort


def make_key(line):
    return int(line.split("\t", 1)[0])


def write_records(path, records):
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(records)


def read_records(path):
    with open(path, encoding="utf-8") as f:
        return f.readlines()


class ExternalSortProperties(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = self._td.name
        self.src = os.path.join(self.dir, "input.txt")
        self.dst = os.path.join(self.dir, "output.txt")

    def tearDown(self):
        self._td.cleanup()

    def sort_and_read(self, records, chunk_size):
        write_records(self.src, records)
        external_sort(self.src, self.dst, make_key, chunk_size=chunk_size,
                      temp_dir=self.dir)
        return read_records(self.dst)

    def assert_conserved_and_stable(self, records, out):
        # 守恒：条数不变
        self.assertEqual(len(out), len(records))
        # 稳定 + 有序：整体等于 Python 稳定排序结果
        self.assertEqual(out, sorted(records, key=make_key))
        # 稳定：逐键检查相同键的相对顺序与输入一致
        def seq(lines):
            order = {}
            for line in lines:
                order.setdefault(make_key(line), []).append(line)
            return order
        self.assertEqual(seq(out), seq(records))

    def test_random_many_duplicate_keys(self):
        rng = random.Random(42)
        records = ["%d\tr%05d\n" % (rng.randrange(10), i) for i in range(5000)]
        out = self.sort_and_read(records, chunk_size=7)
        self.assert_conserved_and_stable(records, out)

    def test_chunk_size_one(self):
        rng = random.Random(7)
        records = ["%d\tr%d\n" % (rng.randrange(5), i) for i in range(200)]
        out = self.sort_and_read(records, chunk_size=1)
        self.assert_conserved_and_stable(records, out)

    def test_empty_file(self):
        out = self.sort_and_read([], chunk_size=4)
        self.assertEqual(out, [])

    def test_single_chunk(self):
        records = ["3\ta\n", "1\tb\n", "2\tc\n"]
        out = self.sort_and_read(records, chunk_size=1000)
        self.assert_conserved_and_stable(records, out)

    def test_single_record(self):
        records = ["9\tonly\n"]
        out = self.sort_and_read(records, chunk_size=1)
        self.assert_conserved_and_stable(records, out)

    def test_all_keys_equal(self):
        records = ["5\tp%03d\n" % i for i in range(300)]
        out = self.sort_and_read(records, chunk_size=11)
        # 全部键相同：稳定排序的输出必须与输入完全一致
        self.assertEqual(out, records)

    def test_no_temp_residue_after_success(self):
        records = ["%d\tr%d\n" % (i % 3, i) for i in range(100)]
        self.sort_and_read(records, chunk_size=4)
        leftovers = [p for p in os.listdir(self.dir) if p.endswith(".run")
                     or p.startswith("extsort_")]
        self.assertEqual(leftovers, [])

    def test_merge_exception_leaves_no_temp_files(self):
        # 归并中途抛异常：split 恰好调用 key n 次（list.sort 每条记录一次），
        # merge 先为 num_runs 个 run 头各调用一次，之后每弹出一条再调用一次。
        # 让第 n + num_runs + 3 次调用爆炸，即归并已经开始之后。
        n, chunk = 100, 10
        num_runs = -(-n // chunk)
        records = ["%d\tr%03d\n" % (i % 10, i) for i in range(n)]
        write_records(self.src, records)
        calls = {"n": 0}
        explode_at = n + num_runs + 3

        def boom_key(line):
            calls["n"] += 1
            if calls["n"] == explode_at:
                raise RuntimeError("simulated merge failure")
            return make_key(line)

        before = set(os.listdir(self.dir))
        with self.assertRaises(RuntimeError):
            external_sort(self.src, self.dst, boom_key, chunk_size=chunk,
                          temp_dir=self.dir)
        after = set(os.listdir(self.dir))
        # 异常路径不残留任何临时文件/目录（output 半成品允许存在，由调用方处理）
        new_entries = after - before - {os.path.basename(self.dst)}
        self.assertEqual(new_entries, set())

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                     "root 会绕过文件权限，无法复现只读目录")
    def test_readonly_temp_dir_raises_and_leaves_no_residue(self):
        records = ["2\ta\n", "1\tb\n"]
        write_records(self.src, records)
        ro_dir = os.path.join(self.dir, "readonly")
        os.mkdir(ro_dir)
        os.chmod(ro_dir, 0o555)
        try:
            with self.assertRaises(OSError):
                external_sort(self.src, self.dst, make_key, chunk_size=1,
                              temp_dir=ro_dir)
            # 只读目录内没有残留，工作目录也没有临时文件
            self.assertEqual(os.listdir(ro_dir), [])
            leftovers = [p for p in os.listdir(self.dir)
                         if p.endswith(".run") or p.startswith("extsort_")]
            self.assertEqual(leftovers, [])
        finally:
            os.chmod(ro_dir, 0o755)


if __name__ == "__main__":
    unittest.main()
