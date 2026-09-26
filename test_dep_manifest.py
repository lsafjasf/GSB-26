#!/usr/bin/env python3
"""dep_manifest 自测：正确性、可复现性、千级组件性能。仅标准库。"""

import json
import random
import time
import unittest

import dep_manifest as dm


def make_lock(packages):
    return {"packages": packages}


class ClosureTest(unittest.TestCase):
    """传递闭包、去重与多路径溯源。"""

    def setUp(self):
        self.deps = {"direct": ["a", "b"]}
        self.lock = make_lock([
            {"name": "a", "version": "1.0", "source": "reg",
             "dependencies": ["c", "d"]},
            {"name": "b", "version": "2.0", "source": "reg",
             "dependencies": ["d"]},
            {"name": "c", "version": "3.0", "source": "reg",
             "dependencies": ["d"]},
            {"name": "d", "version": "4.0", "source": "reg",
             "dependencies": []},
        ])

    def test_transitive_closure(self):
        manifest = dm.build_manifest(self.deps, self.lock)
        names = {c["name"] for c in manifest["components"]}
        self.assertEqual(names, {"a", "b", "c", "d"})

    def test_dedup_and_all_paths(self):
        manifest = dm.build_manifest(self.deps, self.lock)
        entries = [c for c in manifest["components"] if c["name"] == "d"]
        self.assertEqual(len(entries), 1, "同一组件只应出现一次")
        paths = {tuple(p) for p in entries[0]["introduced_by"]}
        self.assertEqual(paths, {("a", "d"), ("a", "c", "d"), ("b", "d")},
                         "必须列出全部引入路径")

    def test_direct_flag(self):
        manifest = dm.build_manifest(self.deps, self.lock)
        direct = {c["name"]: c["direct"] for c in manifest["components"]}
        self.assertEqual(direct, {"a": True, "b": True, "c": False, "d": False})

    def test_cycle_terminates(self):
        lock = make_lock([
            {"name": "x", "version": "1", "source": "s", "dependencies": ["y"]},
            {"name": "y", "version": "1", "source": "s", "dependencies": ["x"]},
        ])
        manifest = dm.build_manifest({"direct": ["x"]}, lock)
        self.assertEqual({c["name"] for c in manifest["components"]}, {"x", "y"})


class MetadataReviewTest(unittest.TestCase):
    """元数据缺失标记：不猜测版本，单独标记待人工确认。"""

    def test_missing_version_and_source(self):
        lock = make_lock([
            {"name": "root", "version": "1", "source": "s",
             "dependencies": ["no-ver", "no-src"]},
            {"name": "no-ver", "version": None, "source": "s",
             "dependencies": []},
            {"name": "no-src", "source": "s", "dependencies": []},
        ])
        manifest = dm.build_manifest({"direct": ["root"]}, lock)
        by_name = {c["name"]: c for c in manifest["components"]}
        self.assertEqual(by_name["no-ver"]["review_reasons"],
                         ["missing_version"])
        self.assertIsNone(by_name["no-ver"]["version"])
        self.assertEqual(by_name["no-src"]["review_reasons"],
                         ["missing_version"])  # 未提供 version 字段
        self.assertTrue(by_name["no-ver"]["needs_review"])

    def test_not_in_lockfile_no_guessing(self):
        manifest = dm.build_manifest({"direct": ["ghost"]}, make_lock([]))
        comp = manifest["components"][0]
        self.assertIsNone(comp["version"], "不得猜测版本")
        self.assertIsNone(comp["source"])
        self.assertIn("not_in_lockfile", comp["review_reasons"])
        self.assertTrue(comp["needs_review"])


class DiffReportTest(unittest.TestCase):
    """两类差异都要报告。"""

    def test_both_directions(self):
        manifest = dm.build_manifest(
            {"direct": ["a"]},
            make_lock([
                {"name": "a", "version": "1", "source": "s",
                 "dependencies": ["gone"]},
                {"name": "gone", "version": "2", "source": "s",
                 "dependencies": []},
            ]))
        artifact = dm.parse_artifact("a@1\nextra@9\n")
        report = dm.diff_report(manifest, artifact)
        self.assertEqual([c["name"] for c in report["missing_in_artifact"]],
                         ["gone"])
        self.assertEqual([c["name"] for c in report["uncovered_in_artifact"]],
                         ["extra"])
        self.assertFalse(report["summary"]["consistent"])

    def test_version_mismatch(self):
        manifest = dm.build_manifest(
            {"direct": ["a"]},
            make_lock([{"name": "a", "version": "1", "source": "s",
                        "dependencies": []}]))
        report = dm.diff_report(manifest, dm.parse_artifact("a@2\n"))
        self.assertEqual(len(report["version_mismatches"]), 1)

    def test_consistent(self):
        manifest = dm.build_manifest(
            {"direct": ["a"]},
            make_lock([{"name": "a", "version": "1", "source": "s",
                        "dependencies": []}]))
        report = dm.diff_report(manifest, dm.parse_artifact("a@1\n"))
        self.assertTrue(report["summary"]["consistent"])


class ReproducibilityTest(unittest.TestCase):
    """同一输入多次生成，清单内容与顺序字节级一致；输入顺序无关。"""

    def test_deterministic_output(self):
        rng = random.Random(42)
        packages = []
        for i in range(300):
            deps = [f"pkg-{j:04d}" for j in
                    rng.sample(range(i + 1, 300), k=min(3, 299 - i))]
            packages.append({"name": f"pkg-{i:04d}", "version": f"1.{i}",
                             "source": "reg", "dependencies": deps})
        deps_doc = {"direct": ["pkg-0000", "pkg-0001", "pkg-0002"]}

        baseline = dm.dump_json(dm.build_manifest(deps_doc, make_lock(packages)))
        for _ in range(5):
            shuffled = packages[:]
            rng.shuffle(shuffled)  # 打乱锁文件条目顺序
            out = dm.dump_json(dm.build_manifest(deps_doc, make_lock(shuffled)))
            self.assertEqual(out, baseline, "输出必须与输入条目顺序无关")

    def test_no_timestamp_fields(self):
        manifest = dm.build_manifest({"direct": []}, make_lock([]))
        self.assertNotIn("generated_at", dm.dump_json(manifest))


class PerformanceTest(unittest.TestCase):
    """上千个组件时的生成耗时。"""

    COMPONENT_COUNT = 3000

    def _synthetic_graph(self, count):
        """随机森林 + 少量指向叶子的共享边（制造多引入路径，且路径总数有界）。"""
        rng = random.Random(2026)
        roots = 20
        edges = {i: [] for i in range(count)}
        # 森林：每个节点挂到前面窗口内的一个父节点
        for i in range(roots, count):
            parent = rng.randrange(max(0, i - 50), i)
            edges[parent].append(i)
        # 共享边：随机内部节点 -> 叶子，每片叶子最多一个额外父节点
        children = {i for i in range(count) for c in edges[i]}
        leaves = [i for i in range(count) if not edges[i]]
        internals = [i for i in range(count) if edges[i]]
        rng.shuffle(leaves)
        for leaf in leaves[:200]:
            src = rng.choice(internals)
            if src != leaf:
                edges[src].append(leaf)
        packages = [{"name": f"comp-{i:05d}", "version": f"1.{i % 50}.0",
                     "source": "registry:test",
                     "dependencies": [f"comp-{j:05d}" for j in sorted(edges[i])]}
                    for i in range(count)]
        direct = [f"comp-{i:05d}" for i in range(roots)]
        return {"direct": direct}, make_lock(packages)

    def test_performance_3k_components(self):
        deps_doc, lock_doc = self._synthetic_graph(self.COMPONENT_COUNT)
        started = time.perf_counter()
        manifest = dm.build_manifest(deps_doc, lock_doc)
        text = dm.dump_json(manifest)
        elapsed = time.perf_counter() - started
        total_paths = sum(len(c["introduced_by"]) for c in manifest["components"])
        print(f"\n[perf] 组件数={manifest['component_count']}, "
              f"引入路径总数={total_paths}, 生成耗时={elapsed:.3f}s, "
              f"清单大小={len(text) / 1024:.0f} KiB")
        self.assertGreaterEqual(manifest["component_count"], 1000)
        self.assertLess(elapsed, 10.0, "3000 组件生成应在 10 秒内完成")


if __name__ == "__main__":
    unittest.main(verbosity=2)
