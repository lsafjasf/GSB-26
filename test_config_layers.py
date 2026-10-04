"""多环境分层配置的回归测试。

覆盖：无覆盖、单层覆盖、多层覆盖、环境 extends 链、列表整体替换、
必需项缺失（任意层 / 指定层）、结构性冲突、文件缺失、继承循环、
非法 JSON、溯源完整性等边界。

Run:  python3 test_config_layers.py -v
"""

import json
import tempfile
import unittest
from pathlib import Path

from config_layers import (
    ConfigError,
    Layer,
    LayerConflictError,
    MissingRequiredError,
    load_environment,
    main,
    merge_layers,
)


def L(name, data):
    return Layer(name, data)


class MergeTest(unittest.TestCase):
    """纯内存层的合并语义。"""

    def test_no_override_single_base_layer(self):
        merged = merge_layers([L("base", {"a": 1, "b": {"c": 2}})])
        self.assertEqual(merged.data, {"a": 1, "b": {"c": 2}})
        self.assertEqual(merged.source_of("a"), "base")
        self.assertEqual(merged.source_of("b.c"), "base")

    def test_single_layer_override(self):
        merged = merge_layers([
            L("base", {"a": 1, "b": 2}),
            L("env:dev", {"b": 20}),
        ])
        self.assertEqual(merged.data, {"a": 1, "b": 20})
        self.assertEqual(merged.source_of("a"), "base")
        self.assertEqual(merged.source_of("b"), "env:dev")

    def test_multi_layer_override_order(self):
        merged = merge_layers([
            L("base", {"x": "base", "y": "base", "z": "base"}),
            L("env:prod", {"y": "env", "z": "env"}),
            L("local", {"z": "local"}),
        ])
        self.assertEqual(merged.data, {"x": "base", "y": "env", "z": "local"})
        self.assertEqual(merged.source_of("x"), "base")
        self.assertEqual(merged.source_of("y"), "env:prod")
        self.assertEqual(merged.source_of("z"), "local")

    def test_deep_merge_nested_dicts_with_per_leaf_provenance(self):
        merged = merge_layers([
            L("base", {"db": {"host": "h0", "port": 1, "pool": {"min": 1, "max": 4}}}),
            L("env:dev", {"db": {"host": "h1", "pool": {"max": 8}}}),
        ])
        self.assertEqual(
            merged.data,
            {"db": {"host": "h1", "port": 1, "pool": {"min": 1, "max": 8}}},
        )
        self.assertEqual(merged.source_of("db.host"), "env:dev")
        self.assertEqual(merged.source_of("db.port"), "base")
        self.assertEqual(merged.source_of("db.pool.min"), "base")
        self.assertEqual(merged.source_of("db.pool.max"), "env:dev")

    def test_list_replaced_wholesale_not_elementwise(self):
        merged = merge_layers([
            L("base", {"sinks": ["a", "b", "c"]}),
            L("local", {"sinks": ["x"]}),
        ])
        self.assertEqual(merged.data["sinks"], ["x"])
        self.assertEqual(merged.source_of("sinks"), "local")

    def test_new_keys_introduced_by_later_layers_are_stamped(self):
        merged = merge_layers([
            L("base", {"a": 1}),
            L("env:dev", {"extra": {"k": "v"}}),
        ])
        self.assertEqual(merged.source_of("extra.k"), "env:dev")

    def test_layers_do_not_mutate_inputs(self):
        base = {"db": {"host": "h"}}
        env = {"db": {"port": 5}}
        merge_layers([L("base", base), L("env:dev", env)])
        self.assertEqual(base, {"db": {"host": "h"}})
        self.assertEqual(env, {"db": {"port": 5}})


class ConflictTest(unittest.TestCase):
    """结构性冲突必须报错并指出问题层，不得静默取默认值。"""

    def test_mapping_vs_scalar_conflict_names_layer_and_path(self):
        with self.assertRaises(LayerConflictError) as ctx:
            merge_layers([
                L("base", {"db": {"host": "h"}}),
                L("env:prod", {"db": "not-a-mapping"}),
            ])
        msg = str(ctx.exception)
        self.assertIn("env:prod", msg)
        self.assertIn("db", msg)

    def test_scalar_vs_mapping_conflict(self):
        with self.assertRaises(LayerConflictError) as ctx:
            merge_layers([
                L("base", {"feature": True}),
                L("local", {"feature": {"nested": 1}}),
            ])
        self.assertIn("local", str(ctx.exception))

    def test_no_layers_is_an_error(self):
        with self.assertRaises(ConfigError):
            merge_layers([])


class RequiredTest(unittest.TestCase):
    """必需项缺失必须报错并指出层。"""

    def setUp(self):
        self.layers = [
            L("base", {"app": {"name": "svc"}}),
            L("env:dev", {"app": {"debug": True}}),
        ]

    def test_required_present_passes(self):
        merged = merge_layers(self.layers, required=["app.name"])
        self.assertEqual(merged.data["app"]["name"], "svc")

    def test_missing_required_any_layer_reports_checked_layers(self):
        with self.assertRaises(MissingRequiredError) as ctx:
            merge_layers(self.layers, required=["db.password"])
        msg = str(ctx.exception)
        self.assertIn("db.password", msg)
        self.assertIn("base", msg)
        self.assertIn("env:dev", msg)

    def test_required_from_specific_layer_ok(self):
        merged = merge_layers(
            self.layers, required=[{"path": "app.debug", "layer": "env:dev"}]
        )
        self.assertEqual(merged.source_of("app.debug"), "env:dev")

    def test_required_from_specific_layer_wrong_source(self):
        with self.assertRaises(MissingRequiredError) as ctx:
            merge_layers(
                self.layers, required=[{"path": "app.name", "layer": "env:dev"}]
            )
        msg = str(ctx.exception)
        self.assertIn("env:dev", msg)
        self.assertIn("base", msg)  # 实际来源层

    def test_required_from_specific_layer_missing_everywhere(self):
        with self.assertRaises(MissingRequiredError) as ctx:
            merge_layers(
                self.layers, required=[{"path": "secret", "layer": "local"}]
            )
        self.assertIn("local", str(ctx.exception))


class ProvenanceTest(unittest.TestCase):
    """溯源数据必须完整覆盖每个生效叶子值。"""

    def test_provenance_map_covers_every_leaf(self):
        merged = merge_layers([
            L("base", {"a": 1, "b": {"c": 2, "d": [1, 2]}}),
            L("env:dev", {"b": {"c": 3}}),
            L("local", {"e": True}),
        ])
        prov = merged.provenance_map()
        self.assertEqual(
            prov,
            {"a": "base", "b.c": "env:dev", "b.d": "base", "e": "local"},
        )

        def leaves(node):
            for value in node.values():
                if isinstance(value, dict):
                    yield from leaves(value)
                else:
                    yield value

        self.assertEqual(len(list(leaves(merged.data))), len(prov))

    def test_source_of_unknown_path_raises(self):
        merged = merge_layers([L("base", {"a": 1})])
        with self.assertRaises(KeyError):
            merged.source_of("nope")
        with self.assertRaises(KeyError):
            merged.source_of("a.b")

    def test_source_of_section_raises(self):
        merged = merge_layers([L("base", {"a": {"b": 1}})])
        with self.assertRaises(KeyError):
            merged.source_of("a")


class LoadEnvironmentTest(unittest.TestCase):
    """基于文件的三层加载与环境继承链。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        (self.dir / "env").mkdir()
        self._write("base.json", {"app": {"name": "svc", "port": 8080},
                                  "db": {"host": "h0", "pool": 5}})

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, rel, data):
        (self.dir / rel).write_text(json.dumps(data), encoding="utf-8")

    def test_env_chain_parent_merges_before_child(self):
        self._write("env/staging.json", {"db": {"host": "staging"}, "x": 1})
        self._write("env/prod.json", {"extends": "staging",
                                      "db": {"host": "prod"}, "x": 2})
        merged = load_environment("prod", self.dir)
        self.assertEqual(merged.layers, ["base", "env:staging", "env:prod"])
        self.assertEqual(merged.data["db"]["host"], "prod")
        self.assertEqual(merged.data["x"], 2)
        self.assertEqual(merged.source_of("db.pool"), "base")

    def test_local_layer_wins_and_is_optional(self):
        self._write("env/dev.json", {"app": {"port": 1}})
        merged = load_environment("dev", self.dir)
        self.assertEqual(merged.layers, ["base", "env:dev"])
        self.assertEqual(merged.data["app"]["port"], 1)

        self._write("local.json", {"app": {"port": 2}})
        merged = load_environment("dev", self.dir)
        self.assertEqual(merged.layers, ["base", "env:dev", "local"])
        self.assertEqual(merged.data["app"]["port"], 2)
        self.assertEqual(merged.source_of("app.port"), "local")

    def test_missing_base_is_an_error(self):
        (self.dir / "base.json").unlink()
        self._write("env/dev.json", {})
        with self.assertRaises(ConfigError) as ctx:
            load_environment("dev", self.dir)
        self.assertIn("base", str(ctx.exception))

    def test_missing_env_file_names_referrer(self):
        with self.assertRaises(ConfigError) as ctx:
            load_environment("ghost", self.dir)
        self.assertIn("ghost", str(ctx.exception))

    def test_missing_extends_target_names_referrer(self):
        self._write("env/prod.json", {"extends": "nowhere"})
        with self.assertRaises(ConfigError) as ctx:
            load_environment("prod", self.dir)
        msg = str(ctx.exception)
        self.assertIn("nowhere", msg)
        self.assertIn("env:prod", msg)

    def test_extends_cycle_is_detected(self):
        self._write("env/a.json", {"extends": "b"})
        self._write("env/b.json", {"extends": "a"})
        with self.assertRaises(ConfigError) as ctx:
            load_environment("a", self.dir)
        self.assertIn("循环", str(ctx.exception))

    def test_invalid_json_names_layer_and_file(self):
        self._write("env/dev.json", {})
        (self.dir / "env" / "dev.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(ConfigError) as ctx:
            load_environment("dev", self.dir)
        self.assertIn("env:dev", str(ctx.exception))

    def test_extends_key_not_leaked_into_merged_data(self):
        self._write("env/prod.json", {"extends": "staging"})
        self._write("env/staging.json", {"k": 1})
        merged = load_environment("prod", self.dir)
        self.assertNotIn("extends", merged.data)

    def test_required_via_loader(self):
        self._write("env/dev.json", {})
        with self.assertRaises(MissingRequiredError):
            load_environment("dev", self.dir, required=["db.password"])
        merged = load_environment("dev", self.dir, required=["db.host"])
        self.assertEqual(merged.source_of("db.host"), "base")


class CliTest(unittest.TestCase):
    """命令行入口：示例配置可直接合并并输出溯源。"""

    def test_repo_sample_config_merges(self):
        repo_root = Path(__file__).resolve().parent
        config_dir = repo_root / "config"
        if not config_dir.is_dir():
            self.skipTest("示例配置目录不存在")
        merged = load_environment("prod", config_dir)
        # prod 继承 staging：db.pool_size 来自 prod，features.new_checkout 来自 staging
        self.assertEqual(merged.source_of("db.pool_size"), "env:prod")
        self.assertEqual(merged.source_of("features.new_checkout"), "env:staging")
        self.assertEqual(merged.source_of("app.name"), "base")
        self.assertEqual(merged.source_of("db.port"), "local")

    def test_cli_returns_nonzero_on_missing_env(self):
        repo_root = Path(__file__).resolve().parent
        rc = main(["no-such-env", "--config-dir", str(repo_root / "config")])
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
