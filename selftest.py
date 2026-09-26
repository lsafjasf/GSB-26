#!/usr/bin/env python3
"""release_check 自测：覆盖全部通过、单项失败、多项失败排序、产物缺失、配置非法。"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import release_check as rc

ROOT = os.path.dirname(os.path.abspath(__file__))


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Fixture:
    """在临时目录构造一套产物 + 清单 + 元数据。"""

    def __init__(self, tmp: str):
        self.tmp = tmp
        os.makedirs(os.path.join(tmp, "artifacts"))
        self.blob = b"release-payload" * 1024
        with open(self._p("artifacts/app.bin"), "wb") as fh:
            fh.write(self.blob)
        self.manifest = {
            "version": "2.0.0",
            "artifacts": [{"path": "artifacts/app.bin", "sha256": sha(self.blob),
                           "size": len(self.blob)}],
        }
        self._write("manifest.json", self.manifest)
        self._write("metadata.json", {"version": "2.0.0", "channel": "stable"})

    def _p(self, rel):
        return os.path.join(self.tmp, rel)

    def _write(self, rel, obj):
        with open(self._p(rel), "w", encoding="utf-8") as fh:
            json.dump(obj, fh)

    def config(self, checks):
        return {"base_dir": self.tmp, "checks": checks}

    def base_checks(self):
        return [
            {"id": "ver", "type": "version_format", "file": "metadata.json",
             "field": "version", "severity": "critical", "fix": "修正版本号"},
            {"id": "channel", "type": "metadata_field", "file": "metadata.json",
             "field": "channel", "equals": "stable", "severity": "minor", "fix": "置为 stable"},
            {"id": "consistency", "type": "manifest_consistency", "manifest": "manifest.json",
             "severity": "critical", "fix": "重新生成产物与清单"},
        ]


class ReleaseCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rc_test_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.fx = Fixture(self.tmp)

    def run_cfg(self, checks):
        cfg = self.fx.config(checks)
        rc.validate_config(cfg)
        return rc.run_checks(cfg, config_dir=self.tmp)

    # 1. 全部通过
    def test_all_pass(self):
        failures, total = self.run_cfg(self.fx.base_checks())
        self.assertEqual(total, 3)
        self.assertEqual(failures, [])

    # 2. 单项失败：元数据字段不符
    def test_single_failure(self):
        self.fx._write("metadata.json", {"version": "2.0.0", "channel": "beta"})
        failures, _ = self.run_cfg(self.fx.base_checks())
        self.assertEqual(len(failures), 1)
        f = failures[0]
        self.assertEqual(f.check_id, "channel")
        self.assertEqual(f.severity, "minor")
        self.assertEqual(f.file, "metadata.json")
        self.assertEqual(f.field, "channel")
        self.assertIn("beta", f.actual)

    # 3. 多项失败：按严重级别排序（critical 在 minor 前）
    def test_multiple_failures_sorted_by_severity(self):
        self.fx._write("metadata.json", {"version": "not-semver", "channel": "beta"})
        failures, _ = self.run_cfg(self.fx.base_checks())
        self.assertEqual(len(failures), 2)
        self.assertEqual([f.severity for f in failures], ["critical", "minor"])
        self.assertEqual(failures[0].check_id, "ver")

    # 4. 产物缺失：manifest 声明了文件但磁盘上没有
    def test_missing_artifact(self):
        os.remove(self.fx._p("artifacts/app.bin"))
        failures, _ = self.run_cfg(self.fx.base_checks())
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].check_id, "consistency")
        self.assertEqual(failures[0].file, "artifacts/app.bin")
        self.assertIn("缺失", failures[0].actual)

    # 5. 校验值绑定真实产物：篡改产物后必须失败，且给出期望/实际值
    def test_checksum_reads_real_artifact(self):
        with open(self.fx._p("artifacts/app.bin"), "wb") as fh:
            fh.write(b"tampered")
        failures, _ = self.run_cfg(self.fx.base_checks())
        self.assertEqual(len(failures), 2)  # sha256 与 size 均不一致
        sha_fail = [f for f in failures if f.field.endswith(".sha256")][0]
        self.assertEqual(sha_fail.expected, sha(self.fx.blob))
        self.assertEqual(sha_fail.actual, sha(b"tampered"))

    # 6. checksum 检查可从声明文件取期望值（expected_from）
    def test_checksum_expected_from_manifest(self):
        checks = [{"id": "sha", "type": "checksum", "path": "artifacts/app.bin",
                   "expected_from": {"file": "manifest.json", "field": "artifacts.0.sha256"},
                   "severity": "critical", "fix": "重建产物"}]
        failures, _ = self.run_cfg(checks)
        self.assertEqual(failures, [])

    # 7. 配置非法：运行前报错，拒绝假通过
    def test_invalid_configs_rejected(self):
        bad = [
            {},  # 无 checks
            {"checks": []},  # 空检查列表
            {"checks": [{"type": "file_exists", "path": "x", "severity": "critical", "fix": "f"}]},  # 缺 id
            {"checks": [{"id": "a", "type": "nope", "severity": "critical", "fix": "f"}]},  # 未知类型
            {"checks": [{"id": "a", "type": "file_exists", "path": "x", "severity": "fatal", "fix": "f"}]},  # 非法级别
            {"checks": [{"id": "a", "type": "file_exists", "path": "x", "severity": "critical", "fix": ""}]},  # 空修复建议
            {"checks": [{"id": "a", "type": "checksum", "path": "x", "severity": "critical", "fix": "f"}]},  # 缺 expected
            {"checks": [{"id": "a", "type": "checksum", "path": "x", "expected": "1",
                         "expected_from": {"file": "m", "field": "f"},
                         "severity": "critical", "fix": "f"}]},  # expected 二选一
            {"checks": [{"id": "a", "type": "file_exists", "path": "x", "severity": "critical", "fix": "f"},
                        {"id": "a", "type": "file_exists", "path": "y", "severity": "critical", "fix": "f"}]},  # id 重复
            {"checks": [{"id": "a", "type": "version_format", "file": "m", "field": "v",
                         "pattern": "([", "severity": "critical", "fix": "f"}]},  # 非法正则
        ]
        for cfg in bad:
            with self.assertRaises(rc.ConfigError, msg=f"应拒绝配置: {cfg}"):
                rc.validate_config(cfg)

    # 8. 端到端 CLI：退出码 0/1/2
    def test_cli_exit_codes(self):
        script = os.path.join(ROOT, "release_check.py")
        ok = subprocess.run([sys.executable, script, "--config", "checks.example.json"],
                            cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertIn("RESULT: PASS", ok.stdout)

        fail = subprocess.run([sys.executable, script, "--config", "checks.failing.example.json"],
                              cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(fail.returncode, 1)
        self.assertIn("RESULT: FAIL", fail.stdout)
        # 失败按严重级别排序：第一个失败必须是 CRITICAL
        first = fail.stdout.splitlines()
        idx = next(i for i, l in enumerate(first) if l.startswith("1. "))
        self.assertIn("CRITICAL", first[idx])

        badcfg = os.path.join(self.tmp, "bad.json")
        with open(badcfg, "w") as fh:
            json.dump({"checks": []}, fh)
        err = subprocess.run([sys.executable, script, "--config", badcfg],
                             capture_output=True, text=True)
        self.assertEqual(err.returncode, 2)
        self.assertIn("CONFIG ERROR", err.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
