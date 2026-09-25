"""外部排序回归测试。

四组内容：
  TestBuggyRepro      —— 针对旧实现，稳定复现四类现网缺陷。
  TestFixedProperties —— 修复后实现的可断言性质（守恒、稳定、异常无残留）。
  TestEdgeCases       —— 空文件 / 单块 / 单记录 / 全部键相同 / 块大小为 1。
  TestMergeInvariants —— 归并不变量的直接断言。
"""

import os
import shutil
import sys
import tempfile
import unittest
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from external_sort import external_sort, _merge_chunks, _split_into_sorted_chunks
from buggy_external_sort import buggy_external_sort


def make_records(key_seq_pairs):
    """(key, seq) -> 行；seq 是全局单调编号，用于校验稳定性与守恒。"""
    return ["%s\t%08d\n" % (k, s) for k, s in key_seq_pairs]


def parse_output(path):
    """返回 [(key, seq), ...]。"""
    pairs = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            k, s = line.rstrip("\n").split("\t")
            pairs.append((k, int(s)))
    return pairs


def assert_stable(testcase, pairs):
    """稳定性不变量：相同键的输出序列中，原始序号严格递增。"""
    seen = defaultdict(list)
    for k, s in pairs:
        seen[k].append(s)
    for k, seqs in seen.items():
        testcase.assertEqual(seqs, sorted(seqs), "key %r 的相对顺序被打乱" % k)


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="extsort-test-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.input_path = os.path.join(self.dir, "input.txt")
        self.output_path = os.path.join(self.dir, "output.txt")

    def write_input(self, lines):
        with open(self.input_path, "w", encoding="utf-8") as f:
            f.writelines(lines)


# ---------------------------------------------------------------- 缺陷复现

class TestBuggyRepro(Base):
    """每个用例都断言旧实现*确实*表现出对应缺陷（修复前红、修复后绿的对照）。"""

    def test_bug1_duplicate_keys_lost_in_merge(self):
        # 两个块都含键 "a"：dict 按 key 覆盖，同键记录丢失。
        lines = make_records([("a", 0), ("b", 1), ("c", 2), ("a", 3), ("d", 4)])
        self.write_input(lines)
        buggy_external_sort(self.input_path, self.output_path, chunk_size=2,
                            temp_dir=self.dir)
        out = parse_output(self.output_path)
        self.assertLess(len(out), len(lines), "缺陷1应导致记录丢失")
        self.assertNotEqual(Counter(k for k, _ in out),
                            Counter(k for k, _ in parse_output(self.input_path)))

    def test_bug2_temp_files_left_after_exception(self):
        lines = make_records([("a", i) for i in range(10)])
        self.write_input(lines)
        bad_output = os.path.join(self.dir, "no-such-dir", "out.txt")
        with self.assertRaises(OSError):
            buggy_external_sort(self.input_path, bad_output, chunk_size=3,
                                temp_dir=self.dir)
        leftovers = [n for n in os.listdir(self.dir) if n.startswith("buggy-chunk-")]
        self.assertTrue(leftovers, "缺陷2应残留临时文件: %s" % leftovers)

    def test_bug3_boundary_records_duplicated(self):
        n = 10
        lines = make_records([("k%03d" % i, i) for i in range(n)])
        self.write_input(lines)
        # 借缺陷2（异常残留）观察缺陷3：让归并阶段的输出打开失败，
        # 残留的 chunk 文件里记录总数应大于输入条数（边界记录被写两次）。
        bad_output = os.path.join(self.dir, "no-such-dir", "out.txt")
        with self.assertRaises(OSError):
            buggy_external_sort(self.input_path, bad_output, chunk_size=3,
                                temp_dir=self.dir)
        chunk_files = [os.path.join(self.dir, f) for f in os.listdir(self.dir)
                       if f.startswith("buggy-chunk-")]
        self.assertTrue(chunk_files)
        total = 0
        for path in chunk_files:
            with open(path, encoding="utf-8") as f:
                total += sum(1 for _ in f)
        self.assertGreater(total, n,
                           "缺陷3应导致边界记录重复写出: %d > %d" % (total, n))

    def test_bug4_stability_broken_within_chunk(self):
        # 全部同键、单块：整行字典序排序会按 seq 字符串重排（seq 故意逆字典序）。
        pairs = [("k", 2), ("k", 10), ("k", 1)]  # 输入顺序 2,10,1
        self.write_input(make_records(pairs))
        buggy_external_sort(self.input_path, self.output_path, chunk_size=100,
                            temp_dir=self.dir)
        out_seqs = [s for _, s in parse_output(self.output_path)]
        self.assertNotEqual(out_seqs, [2, 10, 1], "缺陷4应打乱相同键的相对顺序")


# ---------------------------------------------------------------- 修复后性质

class TestFixedProperties(Base):
    def test_conservation_many_duplicate_keys(self):
        # 大量重复键 + 多种块大小（含 1）：输出条数 == 输入条数，多重集一致。
        import random
        rng = random.Random(42)
        pairs = [("k%03d" % rng.randrange(7), i) for i in range(500)]
        self.write_input(make_records(pairs))
        for chunk_size in (1, 2, 3, 17, 1000):
            written = external_sort(self.input_path, self.output_path,
                                    chunk_size=chunk_size, temp_dir=self.dir)
            out = parse_output(self.output_path)
            self.assertEqual(written, len(pairs))
            self.assertEqual(len(out), len(pairs), "chunk_size=%d 条数不守恒" % chunk_size)
            self.assertEqual(Counter(out), Counter(pairs))
            self.assertEqual([k for k, _ in out], sorted(k for k, _ in pairs))
            assert_stable(self, out)

    def test_stability_preserves_original_relative_order(self):
        # 相同键的记录必须保持原始输入相对顺序。
        pairs = ([("b", i) for i in range(50)]
                 + [("a", 100 + i) for i in range(50)]
                 + [("b", 200 + i) for i in range(50)])
        self.write_input(make_records(pairs))
        external_sort(self.input_path, self.output_path, chunk_size=7,
                      temp_dir=self.dir)
        out = parse_output(self.output_path)
        self.assertEqual([k for k, _ in out], ["a"] * 50 + ["b"] * 100)
        b_seqs = [s for k, s in out if k == "b"]
        self.assertEqual(b_seqs, list(range(50)) + list(range(200, 250)))
        assert_stable(self, out)

    def test_exception_mid_merge_leaves_no_temp_files(self):
        # key_func 在归并阶段（分块完成之后）抛异常。
        pairs = [("k%03d" % (i % 5), i) for i in range(40)]
        self.write_input(make_records(pairs))
        calls = {"n": 0}
        n_records = len(pairs)

        def bomb_key(record):
            calls["n"] += 1
            if calls["n"] > n_records + 3:  # 分块恰好调用 n_records 次，之后即归并
                raise RuntimeError("boom during merge")
            return record.split("\t", 1)[0]

        before = set(os.listdir(self.dir))
        with self.assertRaises(RuntimeError):
            external_sort(self.input_path, self.output_path, chunk_size=3,
                          temp_dir=self.dir, key_func=bomb_key)
        self.assertGreater(calls["n"], n_records, "确认异常发生在归并阶段")
        after = set(os.listdir(self.dir))
        self.assertEqual(before | {"output.txt"}, after,
                         "异常后残留临时文件: %s" % (after - before))

    def test_readonly_temp_dir_raises_and_leaves_nothing(self):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("root 可绕过目录写权限")
        ro_dir = os.path.join(self.dir, "readonly")
        os.mkdir(ro_dir)
        os.chmod(ro_dir, 0o555)
        self.addCleanup(os.chmod, ro_dir, 0o755)
        self.write_input(make_records([("a", 0)]))
        with self.assertRaises(PermissionError):
            external_sort(self.input_path, self.output_path, chunk_size=1,
                          temp_dir=ro_dir)
        self.assertEqual([], os.listdir(ro_dir), "只读目录中不应有任何残留")
        leftovers = [n for n in os.listdir(self.dir) if n.startswith("extsort-")]
        self.assertEqual([], leftovers)

    def test_temp_dir_cleaned_on_success(self):
        self.write_input(make_records([("b", 0), ("a", 1), ("a", 2)]))
        external_sort(self.input_path, self.output_path, chunk_size=1,
                      temp_dir=self.dir)
        leftovers = [n for n in os.listdir(self.dir) if n.startswith("extsort-")]
        self.assertEqual([], leftovers, "正常结束后临时目录应被清理")


# ---------------------------------------------------------------- 边界情形

class TestEdgeCases(Base):
    def run_sort(self, pairs, chunk_size):
        self.write_input(make_records(pairs))
        written = external_sort(self.input_path, self.output_path,
                                chunk_size=chunk_size, temp_dir=self.dir)
        out = parse_output(self.output_path)
        self.assertEqual(written, len(pairs))
        return out

    def test_empty_file(self):
        out = self.run_sort([], chunk_size=4)
        self.assertEqual([], out)
        self.assertTrue(os.path.exists(self.output_path))

    def test_single_record(self):
        out = self.run_sort([("only", 0)], chunk_size=4)
        self.assertEqual([("only", 0)], out)

    def test_single_chunk(self):
        pairs = [("c", 0), ("a", 1), ("b", 2), ("a", 3)]
        out = self.run_sort(pairs, chunk_size=100)
        self.assertEqual([k for k, _ in out], ["a", "a", "b", "c"])
        assert_stable(self, out)

    def test_all_keys_equal(self):
        pairs = [("same", i) for i in range(200)]
        out = self.run_sort(pairs, chunk_size=7)
        self.assertEqual([s for _, s in out], list(range(200)),
                         "全部键相同时输出必须等于原始顺序")

    def test_chunk_size_one(self):
        pairs = [("d", 0), ("b", 1), ("d", 2), ("a", 3), ("b", 4)]
        out = self.run_sort(pairs, chunk_size=1)
        self.assertEqual([k for k, _ in out], ["a", "b", "b", "d", "d"])
        assert_stable(self, out)


# ---------------------------------------------------------------- 归并不变量

class TestMergeInvariants(Base):
    def test_heap_tie_break_by_chunk_index(self):
        # 构造两个已排序块，相同键交错出现；归并后同键必须按块序（=输入先后）输出。
        self.write_input(make_records([("x", 0), ("y", 1), ("x", 2), ("z", 3), ("x", 4)]))
        chunk_paths = _split_into_sorted_chunks(self.input_path, self.dir, 2,
                                                lambda r: r.split("\t", 1)[0])
        self.assertEqual(3, len(chunk_paths))
        written = _merge_chunks(chunk_paths, self.output_path,
                                lambda r: r.split("\t", 1)[0])
        out = parse_output(self.output_path)
        self.assertEqual(written, 5)
        self.assertEqual([k for k, _ in out], ["x", "x", "x", "y", "z"])
        # 不变量 I2：相同键按 chunk_index 升序输出，即原始相对顺序。
        self.assertEqual([s for k, s in out if k == "x"], [0, 2, 4])

    def test_merge_count_equals_sum_of_chunk_counts(self):
        # 不变量 I3（守恒）：归并写出条数 == 各块记录数之和。
        pairs = [("k%02d" % (i % 9), i) for i in range(97)]
        self.write_input(make_records(pairs))
        chunk_paths = _split_into_sorted_chunks(self.input_path, self.dir, 10,
                                                lambda r: r.split("\t", 1)[0])
        total = 0
        for p in chunk_paths:
            with open(p, encoding="utf-8") as f:
                total += sum(1 for _ in f)
        written = _merge_chunks(chunk_paths, self.output_path,
                                lambda r: r.split("\t", 1)[0])
        self.assertEqual(written, total)
        self.assertEqual(written, len(pairs))


if __name__ == "__main__":
    unittest.main(verbosity=2)
