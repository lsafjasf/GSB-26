"""snapshotlib 的自测：链校验 + 恢复对拍。

运行: python3 -m unittest test_snapshotlib -v
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import snapshotlib as sl


def write_tree(root, files):
    """files: {rel_path: bytes|str}，整体重建一个目录树。"""
    if os.path.exists(root):
        shutil.rmtree(root)
    os.makedirs(root)
    for rel, content in files.items():
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path) or root, exist_ok=True)
        data = content.encode() if isinstance(content, str) else content
        with open(path, "wb") as f:
            f.write(data)


def tree_hashes(root):
    """目录树 -> {rel: sha256}，用于对拍。"""
    return {rel: sha for rel, (sha, _size) in sl.scan_dir(root).items()}


def rewrite_manifest(repo, snap_id, **updates):
    """修改清单并重新计算校验值（模拟'合法格式但语义错误'的情形）。"""
    mpath = repo._manifest_path(snap_id)
    with open(mpath, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    manifest.update(updates)
    manifest["manifest_hash"] = sl.compute_manifest_hash(manifest)
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)


class SnapshotTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="snaptest-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.src = os.path.join(self.tmp, "src")
        self.dst = os.path.join(self.tmp, "dst")
        self.repo = sl.BackupRepo(os.path.join(self.tmp, "repo"))

    def assert_restored(self, snap_id, expected_files):
        self.repo.restore(snap_id, self.dst)
        expected = {rel: (content.encode() if isinstance(content, str)
                          else content)
                    for rel, content in expected_files.items()}
        actual_hashes = tree_hashes(self.dst)
        import hashlib
        expected_hashes = {rel: hashlib.sha256(data).hexdigest()
                           for rel, data in expected.items()}
        self.assertEqual(actual_hashes, expected_hashes)

    # ------------------------------------------------ 正常路径

    def test_empty_snapshot(self):
        write_tree(self.src, {})
        snap = self.repo.create_full(self.src)
        self.assert_restored(snap, {})
        # 空目录上的增量也是空快照
        snap2 = self.repo.create_incremental(self.src)
        self.assert_restored(snap2, {})

    def test_single_full(self):
        files = {"a.txt": "hello", "sub/b.bin": b"\x00\x01\x02" * 100}
        write_tree(self.src, files)
        snap = self.repo.create_full(self.src)
        self.assert_restored(snap, files)

    def test_chain_roundtrip_and_restore_from_any_position(self):
        """对拍：每个快照点恢复结果都必须与备份时刻源目录逐条一致。"""
        states = [
            {"a.txt": "v1", "b.txt": "b1"},
            {"a.txt": "v2", "b.txt": "b1", "c/d.txt": "new"},
            {"a.txt": "v2", "c/d.txt": "new2"},          # 删除 b.txt
            {"a.txt": "v2", "c/d.txt": "new2", "e/f/g.txt": "deep"},
            {},                                            # 全部删除
            {"only.txt": "reborn"},
        ]
        snap_ids = []
        for i, files in enumerate(states):
            write_tree(self.src, files)
            if i == 0:
                snap_ids.append(self.repo.create_full(self.src))
            else:
                snap_ids.append(self.repo.create_incremental(self.src))
        # 从链中任意位置恢复，逐一与备份时刻状态对拍
        for snap_id, expected in zip(snap_ids, states):
            self.assert_restored(snap_id, expected)

    def test_incremental_delete(self):
        write_tree(self.src, {"keep.txt": "k", "gone.txt": "g",
                              "dir/nested.txt": "n"})
        full = self.repo.create_full(self.src)
        write_tree(self.src, {"keep.txt": "k"})
        inc = self.repo.create_incremental(self.src)
        manifest = self.repo._load_manifest(inc)
        self.assertEqual(sorted(manifest["changes"]["deletes"]),
                         ["dir/nested.txt", "gone.txt"])
        self.assert_restored(inc, {"keep.txt": "k"})
        self.assert_restored(full, {"keep.txt": "k", "gone.txt": "g",
                                    "dir/nested.txt": "n"})

    def test_long_chain(self):
        files = {}
        snap_ids = []
        for i in range(50):
            files["file_%03d.txt" % i] = "content-%d" % i
            if i > 0 and i % 3 == 0:
                files.pop("file_%03d.txt" % (i - 1), None)
            write_tree(self.src, files)
            if i == 0:
                snap_ids.append(self.repo.create_full(self.src))
            else:
                snap_ids.append(self.repo.create_incremental(self.src))
        report = self.repo.restore(snap_ids[-1], self.dst)
        self.assertEqual(report.chain_length, 50)
        self.assertEqual(tree_hashes(self.dst), tree_hashes(self.src))

    def test_restore_is_idempotent(self):
        write_tree(self.src, {"x.txt": "1"})
        full = self.repo.create_full(self.src)
        write_tree(self.src, {"x.txt": "2", "y.txt": "3"})
        inc = self.repo.create_incremental(self.src)
        self.repo.restore(inc, self.dst)
        first = tree_hashes(self.dst)
        # 重复恢复（目标目录已存在）结果不变
        self.repo.restore(inc, self.dst)
        self.assertEqual(tree_hashes(self.dst), first)
        # 往目标目录塞脏数据后再恢复，结果仍一致
        write_tree(os.path.join(self.dst, "junk"), {"junk.txt": "junk"})
        self.repo.restore(inc, self.dst)
        self.assertEqual(tree_hashes(self.dst), first)
        self.assertFalse(os.path.exists(os.path.join(self.dst, "junk")))

    # ------------------------------------------------ 异常路径（可区分错误）

    def test_missing_parent_snapshot(self):
        write_tree(self.src, {"a": "1"})
        full = self.repo.create_full(self.src)
        write_tree(self.src, {"a": "2"})
        inc1 = self.repo.create_incremental(self.src)
        write_tree(self.src, {"a": "3"})
        inc2 = self.repo.create_incremental(self.src)
        # 删除链中间的快照
        shutil.rmtree(os.path.join(self.repo.snapshots_dir, inc1))
        with self.assertRaises(sl.MissingSnapshotError) as ctx:
            self.repo.restore(inc2, self.dst)
        self.assertEqual(ctx.exception.snapshot_id, inc1)
        self.assertEqual(ctx.exception.referenced_by, inc2)
        self.assertFalse(os.path.exists(self.dst))  # 不产出半成品

    def test_tampered_manifest(self):
        write_tree(self.src, {"a": "1"})
        full = self.repo.create_full(self.src)
        mpath = self.repo._manifest_path(full)
        with open(mpath) as f:
            manifest = json.load(f)
        # 篡改内容但不修正校验值
        manifest["changes"]["upserts"]["evil.txt"] = {
            "sha256": "0" * 64, "size": 1}
        with open(mpath, "w") as f:
            json.dump(manifest, f)
        with self.assertRaises(sl.ChecksumMismatchError):
            self.repo.restore(full, self.dst)

    def test_tampered_object(self):
        write_tree(self.src, {"a.txt": "original"})
        full = self.repo.create_full(self.src)
        sha = tree_hashes(self.src)["a.txt"]
        with open(os.path.join(self.repo.objects_dir, sha), "wb") as f:
            f.write(b"tampered")
        with self.assertRaises(sl.CorruptObjectError) as ctx:
            self.repo.restore(full, self.dst)
        self.assertIn(sha, str(ctx.exception))
        self.assertFalse(os.path.exists(self.dst))

    def test_missing_object(self):
        write_tree(self.src, {"a.txt": "data"})
        full = self.repo.create_full(self.src)
        sha = tree_hashes(self.src)["a.txt"]
        os.remove(os.path.join(self.repo.objects_dir, sha))
        with self.assertRaises(sl.CorruptObjectError):
            self.repo.restore(full, self.dst)

    def test_version_mismatch(self):
        write_tree(self.src, {"a": "1"})
        full = self.repo.create_full(self.src)
        rewrite_manifest(self.repo, full, version=999)
        with self.assertRaises(sl.VersionMismatchError) as ctx:
            self.repo.restore(full, self.dst)
        self.assertEqual(ctx.exception.found, 999)
        self.assertEqual(ctx.exception.expected, sl.FORMAT_VERSION)

    def test_broken_chain(self):
        write_tree(self.src, {"a": "1"})
        full = self.repo.create_full(self.src)
        write_tree(self.src, {"a": "2"})
        inc = self.repo.create_incremental(self.src)
        # 父快照存在，但子快照声明的父校验值被改成别的值
        rewrite_manifest(self.repo, inc, parent_hash="f" * 64)
        with self.assertRaises(sl.BrokenChainError):
            self.repo.restore(inc, self.dst)
        # 全量快照带父引用也是链断裂
        rewrite_manifest(self.repo, full, parent_id="deadbeef",
                         parent_hash="0" * 64)
        with self.assertRaises(sl.BrokenChainError):
            self.repo.restore(full, self.dst)

    def test_incremental_without_any_snapshot(self):
        write_tree(self.src, {"a": "1"})
        with self.assertRaises(sl.ChainError):
            self.repo.create_incremental(self.src)

    def test_restore_unknown_snapshot(self):
        with self.assertRaises(sl.MissingSnapshotError):
            self.repo.restore("no-such-id", self.dst)


if __name__ == "__main__":
    unittest.main()
