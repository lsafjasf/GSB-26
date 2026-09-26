#!/usr/bin/env python3
"""release_check.py 自测：覆盖全部通过 / 单项失败 / 多项失败 / 产物缺失 / 非法配置。"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import release_check as rc


def make_workspace():
    """构造一个最小可用发布目录，返回 (tmpdir, files)。"""
    tmp = tempfile.mkdtemp(prefix="relcheck-")
    payload = b"release-payload" * 64
    with open(os.path.join(tmp, "app.bin"), "wb") as fh:
        fh.write(payload)
    with open(os.path.join(tmp, "VERSION"), "w") as fh:
        fh.write("2.0.0\n")
    with open(os.path.join(tmp, "metadata.json"), "w") as fh:
        json.dump({"name": "app", "version": "2.0.0"}, fh)
    digest = hashlib.sha256(payload).hexdigest()
    with open(os.path.join(tmp, "manifest.json"), "w") as fh:
        json.dump({"artifacts": [{"path": "app.bin", "sha256": digest}]}, fh)
    return tmp, payload, digest


def base_config(tmp, digest):
    return {
        "base_dir": tmp,
        "checks": [
            {"id": "exists", "type": "file_exists", "path": "app.bin",
             "severity": "critical", "fix": "重新打包"},
            {"id": "hash", "type": "checksum", "path": "app.bin",
             "algorithm": "sha256", "expected": digest,
             "severity": "critical", "fix": "更新校验值"},
            {"id": "ver", "type": "version_format", "path": "VERSION",
             "pattern": r"\d+\.\d+\.\d+", "severity": "major",
             "fix": "修正版本号"},
            {"id": "meta", "type": "metadata_field", "path": "metadata.json",
             "field": "version", "expected": "2.0.0", "severity": "major",
             "fix": "同步元数据版本"},
            {"id": "consistency", "type": "artifact_consistency",
             "manifest": "manifest.json", "severity": "critical",
             "fix": "重新生成 manifest"},
        ],
    }


class ReleaseCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp, self.payload, self.digest = make_workspace()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def run_cfg(self, cfg):
        rc.validate_config(cfg)
        failures, total, _ = rc.run_all(cfg)
        return failures, total

    # 1. 全部通过
    def test_all_pass(self):
        failures, total = self.run_cfg(base_config(self.tmp, self.digest))
        self.assertEqual(total, 5)
        self.assertEqual(failures, [])

    # 2. 单项失败：篡改产物后 checksum 与一致性应同时抓到，此处只保留 checksum 项
    def test_single_failure_checksum(self):
        with open(os.path.join(self.tmp, "app.bin"), "ab") as fh:
            fh.write(b"tampered")
        cfg = base_config(self.tmp, self.digest)
        cfg["checks"] = [c for c in cfg["checks"] if c["id"] == "hash"]
        failures, _ = self.run_cfg(cfg)
        self.assertEqual(len(failures), 1)
        f = failures[0]
        self.assertEqual(f.file, "app.bin")
        self.assertEqual(f.field, "sha256")
        self.assertEqual(f.expected, self.digest)
        self.assertNotEqual(f.actual, self.digest)
        self.assertEqual(f.severity, "critical")

    # 3. 多项失败：按严重级别排序，且精确定位字段
    def test_multiple_failures_sorted_by_severity(self):
        with open(os.path.join(self.tmp, "VERSION"), "w") as fh:
            fh.write("v2\n")                       # major
        with open(os.path.join(self.tmp, "metadata.json"), "w") as fh:
            json.dump({"name": "app"}, fh)        # major: version 字段缺失
        with open(os.path.join(self.tmp, "app.bin"), "ab") as fh:
            fh.write(b"x")                          # critical: hash + consistency
        failures, _ = self.run_cfg(base_config(self.tmp, self.digest))
        self.assertGreaterEqual(len(failures), 4)
        ranks = [rc.SEVERITY_RANK[f.severity] for f in failures]
        self.assertEqual(ranks, sorted(ranks), "失败必须按严重级别排序")
        self.assertEqual(failures[0].severity, "critical")
        self.assertEqual(failures[-1].severity, "major")
        fields = {(f.file, f.field) for f in failures}
        self.assertIn(("VERSION", "<content>"), fields)
        self.assertIn(("metadata.json", "version"), fields)

    # 4. 产物缺失：file_exists / checksum / consistency 均须报“文件不存在”
    def test_missing_artifact(self):
        os.remove(os.path.join(self.tmp, "app.bin"))
        failures, _ = self.run_cfg(base_config(self.tmp, self.digest))
        missing = [f for f in failures if f.actual == "文件不存在"]
        self.assertGreaterEqual(len(missing), 3)
        self.assertTrue(all(f.file == "app.bin" for f in missing))

    # 5. 一致性检查必须读真实产物：manifest 声明正确但产物被篡改 -> 失败
    def test_consistency_reads_real_artifact(self):
        with open(os.path.join(self.tmp, "app.bin"), "wb") as fh:
            fh.write(b"completely-different-content")
        cfg = base_config(self.tmp, self.digest)
        cfg["checks"] = [c for c in cfg["checks"] if c["id"] == "consistency"]
        failures, _ = self.run_cfg(cfg)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].field, "sha256")

    # 6. 非法配置：运行前报错
    def test_invalid_configs_rejected(self):
        good = base_config(self.tmp, self.digest)

        with self.assertRaises(rc.ConfigError):
            rc.validate_config({"checks": []})  # 检查项缺失

        bad_type = json.loads(json.dumps(good))
        bad_type["checks"][0]["type"] = "nonsense"
        with self.assertRaises(rc.ConfigError):
            rc.validate_config(bad_type)

        bad_sev = json.loads(json.dumps(good))
        bad_sev["checks"][0]["severity"] = "fatal"
        with self.assertRaises(rc.ConfigError):
            rc.validate_config(bad_sev)

        dup = json.loads(json.dumps(good))
        dup["checks"].append(dict(dup["checks"][0]))
        with self.assertRaises(rc.ConfigError):
            rc.validate_config(dup)

        no_fix = json.loads(json.dumps(good))
        del no_fix["checks"][0]["fix"]
        with self.assertRaises(rc.ConfigError):
            rc.validate_config(no_fix)

        bad_regex = json.loads(json.dumps(good))
        bad_regex["checks"][2]["pattern"] = "(["
        with self.assertRaises(rc.ConfigError):
            rc.validate_config(bad_regex)

        missing_field = json.loads(json.dumps(good))
        del missing_field["checks"][1]["expected"]
        with self.assertRaises(rc.ConfigError):
            rc.validate_config(missing_field)

    # 7. CLI 退出码：0 通过 / 1 失败 / 2 配置非法
    def test_exit_codes(self):
        cfg_path = os.path.join(self.tmp, "cfg.json")
        with open(cfg_path, "w") as fh:
            json.dump(base_config(self.tmp, self.digest), fh)
        self.assertEqual(rc.main(["--config", cfg_path]), 0)

        with open(os.path.join(self.tmp, "app.bin"), "ab") as fh:
            fh.write(b"tamper")
        self.assertEqual(rc.main(["--config", cfg_path]), 1)

        with open(cfg_path, "w") as fh:
            json.dump({"checks": []}, fh)
        self.assertEqual(rc.main(["--config", cfg_path]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
