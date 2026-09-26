"""复现 external_sort_buggy 的四类线上缺陷。

每个测试断言“缺陷症状出现”，因此对 buggy 实现全部通过（即稳定复现），
对修复后的实现全部失败（症状消失）。

运行：python3 -m unittest test_reproduce -v
"""

import os
import tempfile
import unittest

from external_sort_buggy import external_sort_buggy


def make_key(line):
    return int(line.split("\t", 1)[0])


def write_records(path, records):
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(records)


def read_records(path):
    with open(path, encoding="utf-8") as f:
        return f.readlines()


class ReproduceBugs(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = self._td.name
        self.src = os.path.join(self.dir, "input.txt")
        self.dst = os.path.join(self.dir, "output.txt")

    def tearDown(self):
        self._td.cleanup()

    def test_bug1_duplicate_records_lost_in_merge(self):
        # 大量完全相同的记录（相同键），分布在多个 run 中。
        records = ["7\tpayload\n"] * 500
        write_records(self.src, records)
        external_sort_buggy(self.src, self.dst, make_key, chunk_size=50,
                            workdir=self.dir)
        out = read_records(self.dst)
        # 缺陷症状：归并时“与上一条相同”的记录被当作重复丢弃。
        self.assertLess(len(out), len(records),
                        "bug1 未复现：输出条数应少于输入条数")

    def test_bug2_temp_files_left_after_exception(self):
        # 归并中途抛异常：key 函数在第 n+2 次调用时爆炸（split 恰好调用 n 次）。
        records = ["%d\tr%d\n" % (i % 10, i) for i in range(100)]
        write_records(self.src, records)
        calls = {"n": 0}

        def boom_key(line):
            calls["n"] += 1
            if calls["n"] == len(records) + 2:
                raise RuntimeError("simulated merge failure")
            return make_key(line)

        with self.assertRaises(RuntimeError):
            external_sort_buggy(self.src, self.dst, boom_key, chunk_size=1000,
                                workdir=self.dir)
        leftovers = [p for p in os.listdir(self.dir) if p.endswith(".run")]
        # 缺陷症状：异常后临时 run 文件残留在工作目录。
        self.assertTrue(leftovers, "bug2 未复现：异常后应残留临时文件")

    def test_bug3_boundary_record_duplicated(self):
        # 块大小为 1 条记录，键全部不同：任何重复都来自边界 bug。
        records = ["%d\tr%d\n" % (i, i) for i in range(20)]
        write_records(self.src, records)
        external_sort_buggy(self.src, self.dst, make_key, chunk_size=1,
                            workdir=self.dir)
        out = read_records(self.dst)
        # 缺陷症状：块边界记录被重复写出，输出条数大于输入条数。
        self.assertGreater(len(out), len(records),
                           "bug3 未复现：输出条数应大于输入条数")

    def test_bug4_stability_claim_violated(self):
        # 两条相同键的记录 A、B，A 在输入中先于 B。
        # 稳定排序必须保持 A 在 B 前。
        records = ["1\tA\n", "1\tB\n"]
        write_records(self.src, records)
        external_sort_buggy(self.src, self.dst, make_key, chunk_size=1,
                            workdir=self.dir)
        out = read_records(self.dst)
        stable = sorted(records, key=make_key)  # Python sorted 是稳定排序
        # 缺陷症状：声称稳定，但并列时取了更晚 run 的记录，B 被排到 A 前面。
        self.assertNotEqual(out, stable,
                            "bug4 未复现：相同键相对顺序应与稳定排序不同")
        self.assertEqual(out[0], "1\tB\n",
                         "bug4 未复现：后到的记录 B 应先被写出")


if __name__ == "__main__":
    unittest.main()
