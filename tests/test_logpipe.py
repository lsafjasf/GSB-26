"""Self tests for logpipe: validation, failure isolation, equivalence, compat.

Run: python3 -m unittest discover -s tests -v
"""

import csv
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from logpipe import LineError, parse_line  # noqa: E402
from logpipe.config import ConfigError, loads_config  # noqa: E402
from logpipe.core import run_pipeline  # noqa: E402
from logpipe.formats import render_value  # noqa: E402
from logpipe.legacy import legacy_config, legacy_parse, run_legacy  # noqa: E402


VALID_CONFIG = {
    "version": 1,
    "parser": {
        "delimiter": "|",
        "trim": True,
        "fields": [
            {"name": "ts", "type": "string", "required": True},
            {"name": "level", "type": "string", "required": True},
            {"name": "pid", "type": "int", "required": True},
            {"name": "latency", "type": "float", "required": False},
            {"name": "ok", "type": "bool", "required": False, "default": True},
            {
                "name": "msg",
                "type": "string",
                "required": False,
                "default": "",
                "greedy": True,
            },
        ],
    },
    "output": {"format": "jsonl"},
}

SAMPLE_LINES = [
    "2026-09-26T10:00:01|INFO|4217|12.5|true|user login ok\n",
    "2026-09-26T10:00:02|WARN|4217|3.1|false|retrying upstream|attempt 2\n",
    "2026-09-26T10:00:04|INFO|4218||true|cache hit\n",
]


def config_dict():
    return json.loads(json.dumps(VALID_CONFIG))


class ConfigValidationTest(unittest.TestCase):
    def test_valid_config_loads(self):
        cfg = loads_config(json.dumps(VALID_CONFIG))
        self.assertEqual(cfg.delimiter, "|")
        self.assertEqual(
            [f.name for f in cfg.fields],
            ["ts", "level", "pid", "latency", "ok", "msg"],
        )
        self.assertEqual(cfg.out_columns[0], ("ts", "ts"))

    def test_duplicate_field_reports_both_positions(self):
        bad = config_dict()
        bad["parser"]["fields"][2]["name"] = "level"
        with self.assertRaises(ConfigError) as cm:
            loads_config(json.dumps(bad))
        message = str(cm.exception)
        self.assertIn("parser.fields[2].name", message)
        self.assertIn("parser.fields[1].name", message)

    def test_required_and_optional_flags_conflict(self):
        bad = config_dict()
        bad["parser"]["fields"][3]["optional"] = False
        with self.assertRaises(ConfigError) as cm:
            loads_config(json.dumps(bad))
        self.assertIn("parser.fields[3]", str(cm.exception))
        self.assertIn("conflicting flags", str(cm.exception))

        also_bad = config_dict()
        also_bad["parser"]["fields"][3]["required"] = True
        also_bad["parser"]["fields"][3]["optional"] = True
        with self.assertRaises(ConfigError) as cm:
            loads_config(json.dumps(also_bad))
        self.assertIn("conflicting flags", str(cm.exception))

    def test_required_field_with_default_conflicts(self):
        bad = config_dict()
        bad["parser"]["fields"][0]["default"] = "now"
        with self.assertRaises(ConfigError) as cm:
            loads_config(json.dumps(bad))
        self.assertIn("parser.fields[0]", str(cm.exception))
        self.assertIn("required but declares a default", str(cm.exception))

    def test_unknown_type_points_to_field(self):
        bad = config_dict()
        bad["parser"]["fields"][3]["type"] = "datetime"
        with self.assertRaises(ConfigError) as cm:
            loads_config(json.dumps(bad))
        message = str(cm.exception)
        self.assertIn("parser.fields[3].type", message)
        self.assertIn("'datetime'", message)

    def test_unknown_output_format(self):
        bad = config_dict()
        bad["output"] = {"format": "yaml"}
        with self.assertRaises(ConfigError) as cm:
            loads_config(json.dumps(bad))
        self.assertIn("output.format", str(cm.exception))

    def test_output_template_unknown_field_and_duplicate(self):
        bad = config_dict()
        bad["output"] = {"format": "jsonl", "fields": ["ts", "ts", "nope"]}
        with self.assertRaises(ConfigError) as cm:
            loads_config(json.dumps(bad))
        message = str(cm.exception)
        self.assertIn("output.fields[2].name", message)
        self.assertIn("unknown field 'nope'", message)
        self.assertIn("duplicate output column", message)

    def test_greedy_only_allowed_on_last_field(self):
        bad = config_dict()
        bad["parser"]["fields"][0]["greedy"] = True
        with self.assertRaises(ConfigError) as cm:
            loads_config(json.dumps(bad))
        self.assertIn("parser.fields[0].greedy", str(cm.exception))

    def test_invalid_json_reports_line_and_column(self):
        with self.assertRaises(ConfigError) as cm:
            loads_config('{"parser": {"fields": [\n}', source="bad.json")
        self.assertIn("bad.json:2:", str(cm.exception))


class ParsingTest(unittest.TestCase):
    def setUp(self):
        self.cfg = loads_config(json.dumps(VALID_CONFIG))

    def test_types_defaults_and_greedy(self):
        record = parse_line(SAMPLE_LINES[0].strip(), self.cfg)
        self.assertEqual(record["ts"], "2026-09-26T10:00:01")
        self.assertEqual(record["pid"], 4217)
        self.assertEqual(record["latency"], 12.5)
        self.assertIs(record["ok"], True)
        self.assertEqual(record["msg"], "user login ok")

        greedy = parse_line(SAMPLE_LINES[1].strip(), self.cfg)
        self.assertEqual(greedy["msg"], "retrying upstream|attempt 2")

        missing_optional = parse_line(SAMPLE_LINES[2].strip(), self.cfg)
        self.assertIsNone(missing_optional["latency"])

    def test_missing_required_fails(self):
        with self.assertRaises(LineError) as cm:
            parse_line("2026-09-26T10:00:05|INFO", self.cfg)
        self.assertIn("expected 6 fields, got 2", str(cm.exception))

        with self.assertRaises(LineError) as cm:
            parse_line("2026-09-26T10:00:06||4218|1|true|x", self.cfg)
        self.assertIn("missing required field 'level'", str(cm.exception))

    def test_bad_type_fails_with_reason(self):
        with self.assertRaises(LineError) as cm:
            parse_line("2026-09-26T10:00:03|ERROR|nope|8.0|true|x", self.cfg)
        self.assertIn("field 'pid'", str(cm.exception))
        self.assertIn("to int", str(cm.exception))


class PipelineFailureIsolationTest(unittest.TestCase):
    def test_bad_lines_reported_and_counted_good_lines_continue(self):
        cfg = loads_config(json.dumps(VALID_CONFIG))
        lines = SAMPLE_LINES + [
            "2026-09-26T10:00:03|ERROR|nope|8.0|true|bad pid\n",
            "2026-09-26T10:00:05|INFO\n",
            "2026-09-26T10:00:06|INFO|4219|1.0|false|after failures\n",
        ]
        out, err = io.StringIO(), io.StringIO()
        stats = run_pipeline(lines, cfg, out, err)

        self.assertEqual(stats.total, 6)
        self.assertEqual(stats.failed, 2)
        self.assertEqual(stats.ok, 4)

        success_rows = [json.loads(l) for l in out.getvalue().splitlines()]
        self.assertEqual(
            [r["msg"] for r in success_rows],
            [
                "user login ok",
                "retrying upstream|attempt 2",
                "cache hit",
                "after failures",
            ],
        )

        failures = [json.loads(l) for l in err.getvalue().splitlines()]
        self.assertEqual([f["line"] for f in failures], [4, 5])
        self.assertIn("pid", failures[0]["error"])
        self.assertIn("expected 6 fields", failures[1]["error"])
        self.assertTrue(all(f["raw"] for f in failures))


class FormatEquivalenceTest(unittest.TestCase):
    def test_jsonl_and_csv_have_same_values_and_field_order(self):
        jsonl_out, csv_out, err = io.StringIO(), io.StringIO(), io.StringIO()
        cfg_jsonl = loads_config(json.dumps(VALID_CONFIG))
        cfg_csv = loads_config(json.dumps(VALID_CONFIG))
        cfg_csv.out_format = "csv"

        run_pipeline(SAMPLE_LINES, cfg_jsonl, jsonl_out, err)
        run_pipeline(SAMPLE_LINES, cfg_csv, csv_out, err)

        records = [json.loads(l) for l in jsonl_out.getvalue().splitlines()]
        rows = list(csv.reader(io.StringIO(csv_out.getvalue())))
        header, data_rows = rows[0], rows[1:]

        self.assertEqual(header, list(records[0].keys()))
        self.assertEqual(len(data_rows), len(records))
        for record, data_row in zip(records, data_rows):
            self.assertEqual(list(record.keys()), header)
            cells = [render_value(value) for value in record.values()]
            self.assertEqual(data_row, cells)

    def test_output_template_reorder_and_rename(self):
        cfg_dict = config_dict()
        cfg_dict["output"] = {
            "format": "jsonl",
            "fields": [{"name": "pid", "as": "process_id"}, "level"],
        }
        cfg = loads_config(json.dumps(cfg_dict))
        out, err = io.StringIO(), io.StringIO()
        run_pipeline([SAMPLE_LINES[0]], cfg, out, err)
        record = json.loads(out.getvalue())
        self.assertEqual(list(record.keys()), ["process_id", "level"])
        self.assertEqual(record, {"process_id": 4217, "level": "INFO"})


class LegacyCompatibilityTest(unittest.TestCase):
    def test_legacy_fixed_split(self):
        out = io.StringIO()
        total = run_legacy(["a|b|c\n", "d|e|f|g\n"], "|", out)
        rows = [json.loads(l) for l in out.getvalue().splitlines()]
        self.assertEqual(total, 2)
        self.assertEqual(rows[0], {"col1": "a", "col2": "b", "col3": "c"})
        self.assertEqual(rows[1]["col4"], "g")

    def test_generated_config_is_valid_and_parses_like_legacy(self):
        generated = legacy_config(3, "|")
        cfg = loads_config(json.dumps(generated))
        record = parse_line("a|b|c", cfg)
        self.assertEqual(record, legacy_parse("a|b|c", "|"))


class CliTest(unittest.TestCase):
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def run_cli(self, args, stdin_text=None):
        env = dict(os.environ, PYTHONPATH=self.ROOT)
        return subprocess.run(
            [sys.executable, "-m", "logpipe", *args],
            input=stdin_text,
            capture_output=True,
            text=True,
            cwd=self.ROOT,
            env=env,
        )

    def _write_rules(self, directory, config):
        path = os.path.join(directory, "rules.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(config, fh)
        return path

    def test_cli_jsonl_and_csv_are_equivalent(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules_path = self._write_rules(tmp, VALID_CONFIG)
            input_text = "".join(SAMPLE_LINES) + "bad-line\n"
            jsonl = self.run_cli(
                ["--rules", rules_path, "--format", "jsonl"], input_text
            )
            csv_run = self.run_cli(
                ["--rules", rules_path, "--format", "csv"], input_text
            )

        self.assertEqual(jsonl.returncode, 0, jsonl.stderr)
        self.assertEqual(csv_run.returncode, 0, csv_run.stderr)
        self.assertIn("failed=1", jsonl.stderr)
        records = [json.loads(l) for l in jsonl.stdout.splitlines()]
        rows = list(csv.reader(io.StringIO(csv_run.stdout)))
        self.assertEqual(rows[0], list(records[0].keys()))
        for record, data_row in zip(records, rows[1:]):
            self.assertEqual(
                data_row, [render_value(v) for v in record.values()]
            )

    def test_cli_strict_exit_code_and_error_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules_path = self._write_rules(tmp, VALID_CONFIG)
            err_path = os.path.join(tmp, "errors.jsonl")
            result = self.run_cli(
                ["--rules", rules_path, "--strict", "--errors", err_path],
                "bad-line\n",
            )
            with open(err_path, encoding="utf-8") as fh:
                error_records = [json.loads(l) for l in fh if l.strip()]
        self.assertEqual(result.returncode, 3)
        self.assertEqual(error_records[0]["line"], 1)
        self.assertIn("expected 6 fields", error_records[0]["error"])

    def test_cli_check_rejects_invalid_config_with_position(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = config_dict()
            bad["parser"]["fields"][1]["name"] = "ts"
            bad_path = self._write_rules(tmp, bad)
            result = self.run_cli(["--rules", bad_path, "--check"])
        self.assertEqual(result.returncode, 2)
        self.assertIn("parser.fields[1].name", result.stderr)
        self.assertIn("duplicate field name", result.stderr)

    def test_cli_check_accepts_valid_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules_path = self._write_rules(tmp, VALID_CONFIG)
            result = self.run_cli(["--rules", rules_path, "--check"])
        self.assertEqual(result.returncode, 0)
        self.assertIn("config OK", result.stdout)

    def test_cli_legacy_mode(self):
        result = self.run_cli(["--legacy"], "a|b\nc|d\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = [json.loads(l) for l in result.stdout.splitlines()]
        self.assertEqual(rows, [{"col1": "a", "col2": "b"},
                                {"col1": "c", "col2": "d"}])

    def test_cli_dump_legacy_config_is_valid(self):
        result = self.run_cli(
            ["--dump-legacy-config", "--legacy-fields", "2"]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        cfg = loads_config(result.stdout)
        self.assertEqual([f.name for f in cfg.fields], ["col1", "col2"])


if __name__ == "__main__":
    unittest.main()
