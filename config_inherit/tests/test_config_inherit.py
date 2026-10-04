"""config_inherit 回归测试：python3 -m unittest discover -s config_inherit/tests -v"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from config_inherit import (  # noqa: E402
    CycleError,
    LayerError,
    MissingRequiredError,
    TypeConflictError,
    load_config,
)


class ConfigTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cfgtest_")
        os.makedirs(os.path.join(self.dir, "envs"))
        self.addCleanup(shutil.rmtree, self.dir, True)

    def write(self, relpath, data):
        path = os.path.join(self.dir, relpath)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if isinstance(data, str):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(data)
        else:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh)

    # ---------- 情形 1：无覆盖，全部来自 base ----------
    def test_no_override(self):
        self.write("base.json", {"a": 1, "b": {"c": "x"}})
        self.write("envs/dev.json", {})
        merged, prov = load_config(self.dir, "dev", local=False)
        self.assertEqual(merged, {"a": 1, "b": {"c": "x"}})
        self.assertEqual(prov["values"]["a"]["layer"], "base")
        self.assertEqual(prov["values"]["b.c"]["layer"], "base")
        self.assertEqual(prov["layer_order"], ["base", "env:dev"])

    # ---------- 情形 2：单层覆盖 ----------
    def test_single_layer_override(self):
        self.write("base.json", {"a": 1, "b": 2})
        self.write("envs/dev.json", {"a": 10})
        merged, prov = load_config(self.dir, "dev", local=False)
        self.assertEqual(merged, {"a": 10, "b": 2})
        self.assertEqual(prov["values"]["a"]["layer"], "env:dev")
        self.assertEqual(
            [h["value"] for h in prov["values"]["a"]["history"]], [1, 10]
        )
        self.assertEqual(prov["values"]["b"]["layer"], "base")

    # ---------- 情形 3：多层覆盖（base -> 父环境 -> 子环境 -> local） ----------
    def test_multi_layer_override_and_inheritance(self):
        self.write("base.json", {"a": 1, "b": 1, "c": 1, "d": 1})
        self.write("envs/prod.json", {"a": 2, "b": 2})
        self.write("envs/prod.cn.json", {"__inherits__": "prod", "a": 3})
        self.write("local.json", {"b": 4})
        merged, prov = load_config(self.dir, "prod.cn")
        self.assertEqual(merged, {"a": 3, "b": 4, "c": 1, "d": 1})
        self.assertEqual(
            prov["layer_order"], ["base", "env:prod", "env:prod.cn", "local"]
        )
        self.assertEqual(prov["values"]["a"]["layer"], "env:prod.cn")
        self.assertEqual(
            [h["layer"] for h in prov["values"]["a"]["history"]],
            ["base", "env:prod", "env:prod.cn"],
        )
        self.assertEqual(prov["values"]["b"]["layer"], "local")
        self.assertEqual(prov["values"]["c"]["layer"], "base")

    # ---------- 情形 4：必需项缺失 ----------
    def test_missing_required_raises(self):
        self.write("base.json", {"__required__": ["db.host", "db.port"], "db": {"port": 5432}})
        self.write("envs/dev.json", {})
        with self.assertRaises(MissingRequiredError) as ctx:
            load_config(self.dir, "dev", local=False)
        self.assertIn("db.host", str(ctx.exception))
        self.assertIn("base", str(ctx.exception))  # 报错需列出已检查的层

    def test_required_null_raises(self):
        self.write("base.json", {"__required__": ["db.host"], "db": {"host": None}})
        self.write("envs/dev.json", {})
        with self.assertRaises(MissingRequiredError):
            load_config(self.dir, "dev", local=False)

    def test_required_filled_by_env_ok(self):
        self.write("base.json", {"__required__": ["db.host"], "db": {"port": 5432}})
        self.write("envs/dev.json", {"db": {"host": "dev-db"}})
        merged, prov = load_config(self.dir, "dev", local=False)
        self.assertEqual(merged["db"]["host"], "dev-db")
        self.assertEqual(prov["values"]["db.host"]["layer"], "env:dev")

    # ---------- 边界：深合并与列表整体替换 ----------
    def test_deep_merge_and_list_replace(self):
        self.write("base.json", {"db": {"host": "h0", "port": 1}, "ips": ["a", "b"]})
        self.write("envs/dev.json", {"db": {"port": 2}, "ips": ["c"]})
        merged, prov = load_config(self.dir, "dev", local=False)
        self.assertEqual(merged["db"], {"host": "h0", "port": 2})
        self.assertEqual(merged["ips"], ["c"])
        self.assertEqual(prov["values"]["db.host"]["layer"], "base")
        self.assertEqual(prov["values"]["db.port"]["layer"], "env:dev")

    # ---------- 边界：类型冲突必须报错并指出两层 ----------
    def test_type_conflict_raises(self):
        self.write("base.json", {"db": {"host": "h"}})
        self.write("envs/dev.json", {"db": "not-a-dict"})
        with self.assertRaises(TypeConflictError) as ctx:
            load_config(self.dir, "dev", local=False)
        msg = str(ctx.exception)
        self.assertIn("env:dev", msg)
        self.assertIn("base", msg)

    # ---------- 边界：继承环 ----------
    def test_inheritance_cycle_raises(self):
        self.write("base.json", {"a": 1})
        self.write("envs/a.json", {"__inherits__": "b"})
        self.write("envs/b.json", {"__inherits__": "a"})
        with self.assertRaises(CycleError):
            load_config(self.dir, "a", local=False)

    def test_self_inheritance_cycle_raises(self):
        self.write("base.json", {"a": 1})
        self.write("envs/a.json", {"__inherits__": "a"})
        with self.assertRaises(CycleError):
            load_config(self.dir, "a", local=False)

    # ---------- 边界：层文件缺失 / 非法 JSON / 非法环境名 ----------
    def test_missing_env_file_raises(self):
        self.write("base.json", {"a": 1})
        with self.assertRaises(LayerError) as ctx:
            load_config(self.dir, "ghost", local=False)
        self.assertIn("env:ghost", str(ctx.exception))

    def test_missing_base_raises(self):
        self.write("envs/dev.json", {})
        with self.assertRaises(LayerError) as ctx:
            load_config(self.dir, "dev", local=False)
        self.assertIn("base", str(ctx.exception))

    def test_invalid_json_raises(self):
        self.write("base.json", "{not json")
        self.write("envs/dev.json", {})
        with self.assertRaises(LayerError) as ctx:
            load_config(self.dir, "dev", local=False)
        self.assertIn("base", str(ctx.exception))

    def test_bad_env_name_raises(self):
        self.write("base.json", {"a": 1})
        with self.assertRaises(LayerError):
            load_config(self.dir, "../etc/passwd", local=False)

    # ---------- 边界：local 层可选 ----------
    def test_local_absent_is_ok(self):
        self.write("base.json", {"a": 1})
        self.write("envs/dev.json", {})
        merged, prov = load_config(self.dir, "dev")  # local=True 但文件不存在
        self.assertEqual(merged, {"a": 1})
        self.assertFalse(prov["local_layer"]["present"])
        self.assertNotIn("local", prov["layer_order"])

    def test_local_disabled(self):
        self.write("base.json", {"a": 1})
        self.write("envs/dev.json", {})
        self.write("local.json", {"a": 99})
        merged, prov = load_config(self.dir, "dev", local=False)
        self.assertEqual(merged["a"], 1)

    # ---------- 边界：保留键不进合并结果 ----------
    def test_reserved_keys_stripped(self):
        self.write("base.json", {"__required__": [], "a": 1})
        self.write("envs/dev.json", {"b": 2})
        merged, _ = load_config(self.dir, "dev", local=False)
        self.assertEqual(merged, {"a": 1, "b": 2})

    # ---------- 边界：溯源记录文件路径 ----------
    def test_provenance_records_file(self):
        self.write("base.json", {"a": 1})
        self.write("envs/dev.json", {"a": 2})
        _, prov = load_config(self.dir, "dev", local=False)
        entry = prov["values"]["a"]
        self.assertTrue(entry["file"].endswith(os.path.join("envs", "dev.json")))
        self.assertEqual(len(entry["history"]), 2)


if __name__ == "__main__":
    unittest.main()
