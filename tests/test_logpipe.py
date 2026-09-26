"""logpipe 自测：等价性 / 失败隔离 / 配置校验 / 旧模式兼容。

运行：python3 -m unittest discover -s tests -v
"""
import csv
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from logpipe.config import ConfigError, load_output, load_rules  # noqa: E402
from logpipe.parser import parse_line  # noqa: E402

RULES = os.path.join(ROOT, "config", "rules.example.json")
OUT_JSONL = os.path.join(ROOT, "config", "output.jsonl.example.json")
OUT_CSV = os.path.join(ROOT, "config", "output.csv.example.json")

SAMPLE_INPUT = (
    "2026-09-26T10:00:01|INFO|12|true|服务启动\n"
    "2026-09-26T10:00:02|WARN|not_an_int|true|延迟异常\n"   # 类型错误 -> 失败行
    "2026-09-26T10:00:03|ERROR|250|false|数据库超时\n"
    "2026-09-26T10:00:04|INFO\n"                              # 缺列 -> 失败行
    "2026-09-26T10:00:05|INFO|3|yes|健康检查通过\n"
)


def run_cli(args, stdin_text):
    return subprocess.run(
        [sys.executable, "-m", "logpipe", *args],
        input=stdin_text, capture_output=True, text=True, cwd=ROOT,
    )


class EquivalenceTest(unittest.TestCase):
    """同一输入分别输出 jsonl 与 csv，字段值与顺序语义必须一致。"""

    def test_jsonl_csv_semantic_equivalence(self):
        with tempfile.TemporaryDirectory() as tmp:
            err_jsonl = os.path.join(tmp, "e1")
            err_csv = os.path.join(tmp, "e2")
            r1 = run_cli(["--rules", RULES, "--output", OUT_JSONL,
                          "--errors", err_jsonl], SAMPLE_INPUT)
            r2 = run_cli(["--rules", RULES, "--output", OUT_CSV,
                          "--errors", err_csv], SAMPLE_INPUT)
            with open(err_jsonl, encoding="utf-8") as fh:
                err_lines_jsonl = [json.loads(l)["line"] for l in fh]
            with open(err_csv, encoding="utf-8") as fh:
                err_lines_csv = [json.loads(l)["line"] for l in fh]
        self.assertEqual(r1.returncode, 0, r1.stderr)
        self.assertEqual(r2.returncode, 0, r2.stderr)

        jsonl_records = [json.loads(line) for line in r1.stdout.strip().splitlines()]
        csv_rows = list(csv.reader(io.StringIO(r2.stdout)))
        header, csv_records = csv_rows[0], csv_rows[1:]

        self.assertEqual(len(jsonl_records), len(csv_records))
        for jrec, crow in zip(jsonl_records, csv_records):
            crec = dict(zip(header, crow))
            self.assertEqual(list(jrec.keys()), header)  # 字段顺序一致
            for key, jval in jrec.items():
                cval = crec[key]
                if jval is None:
                    self.assertEqual(cval, "")
                elif isinstance(jval, bool):
                    self.assertEqual(cval, "true" if jval else "false")
                else:
                    self.assertEqual(cval, str(jval))

        # 两者的失败行集合也必须一致
        self.assertEqual(err_lines_jsonl, err_lines_csv)


class FailureIsolationTest(unittest.TestCase):
    def test_bad_lines_do_not_abort_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            err_path = os.path.join(tmp, "errors.jsonl")
            result = run_cli(["--rules", RULES, "--output", OUT_JSONL,
                              "--errors", err_path], SAMPLE_INPUT)
            with open(err_path, encoding="utf-8") as fh:
                errors = [json.loads(l) for l in fh]
        self.assertEqual(result.returncode, 0)
        good = [json.loads(l) for l in result.stdout.strip().splitlines()]
        self.assertEqual(len(good), 3)  # 5 行中 3 行成功，批处理未中断
        self.assertEqual(good[0]["latency_ms"], 12)
        self.assertIs(good[0]["ok"], True)
        self.assertEqual([e["line"] for e in errors], [2, 4])
        self.assertIn("int", errors[0]["error"])
        self.assertIn("必填", errors[1]["error"])
        self.assertIn("failed=2", result.stderr)  # 失败计数

    def test_strict_exit_code(self):
        result = run_cli(["--rules", RULES, "--strict"], SAMPLE_INPUT)
        self.assertEqual(result.returncode, 1)

    def test_optional_default_applied(self):
        rules = load_rules(RULES)
        rec = parse_line("2026-09-26T10:00:09|INFO||true|无延迟信息\n", rules)
        self.assertEqual(rec["latency_ms"], 0)  # default 生效
        rec2 = parse_line("2026-09-26T10:00:10|INFO|5||布尔缺省\n", rules)
        self.assertIsNone(rec2["ok"])  # 无 default 的可选字段为 None


class ConfigValidationTest(unittest.TestCase):
    def _write(self, tmp, name, data):
        path = os.path.join(tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return path

    def test_duplicate_field_name_reports_position(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "rules.json", {"parse": {"fields": [
                {"name": "a"}, {"name": "b"}, {"name": "a"}]}})
            with self.assertRaises(ConfigError) as ctx:
                load_rules(path)
        self.assertIn("parse.fields[2].name", str(ctx.exception))
        self.assertIn("parse.fields[0]", str(ctx.exception))

    def test_required_with_default_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "rules.json", {"parse": {"fields": [
                {"name": "a", "required": True, "default": "x"}]}})
            with self.assertRaises(ConfigError) as ctx:
                load_rules(path)
        self.assertIn("parse.fields[0]", str(ctx.exception))
        self.assertIn("冲突", str(ctx.exception))

    def test_unknown_type_reports_position(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "rules.json", {"parse": {"fields": [
                {"name": "a"}, {"name": "b", "type": "datetime"}]}})
            with self.assertRaises(ConfigError) as ctx:
                load_rules(path)
        self.assertIn("parse.fields[1].type", str(ctx.exception))
        self.assertIn("datetime", str(ctx.exception))

    def test_output_template_unknown_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules = load_rules(RULES)
            path = self._write(tmp, "out.json",
                               {"format": "jsonl", "fields": ["ts", "nope"]})
            with self.assertRaises(ConfigError) as ctx:
                load_output(path, rules)
        self.assertIn("output.fields[1]", str(ctx.exception))

    def test_cli_reports_config_error_with_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = self._write(tmp, "bad.json", {"parse": {"fields": [
                {"name": "x", "type": "weird"}]}})
            result = run_cli(["--rules", bad], "anything\n")
        self.assertEqual(result.returncode, 2)
        self.assertIn("parse.fields[0].type", result.stderr)


class LegacyCompatTest(unittest.TestCase):
    def test_legacy_default_when_no_rules(self):
        text = "2026-09-26T10:00:01 INFO 服务 启动\n坏行\n"
        result = run_cli([], text)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout,
                         "ts=2026-09-26T10:00:01 level=INFO msg=服务 启动\n")
        self.assertIn("line 2", result.stderr)
        self.assertIn("failed=1", result.stderr)

    def test_legacy_flag_explicit(self):
        result = run_cli(["--legacy"], "2026-09-26T10:00:01 WARN 磁盘 不足\n")
        self.assertEqual(result.stdout,
                         "ts=2026-09-26T10:00:01 level=WARN msg=磁盘 不足\n")


if __name__ == "__main__":
    unittest.main()
