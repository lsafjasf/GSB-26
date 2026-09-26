#!/usr/bin/env python3
"""发布前检查清单自动化脚本（仅标准库）。

用法:
    python3 release_check.py --config checks.json [--json report.json]

退出码:
    0  全部检查通过
    1  存在检查失败
    2  配置非法 / 检查项缺失（运行前报错，拒绝假通过）
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time

SEVERITIES = ["critical", "major", "minor"]
SEVERITY_RANK = {name: idx for idx, name in enumerate(SEVERITIES)}
HASH_ALGORITHMS = {"sha256", "sha512", "md5"}
CHECK_TYPES = {
    "file_exists",
    "checksum",
    "version_format",
    "metadata_field",
    "artifact_consistency",
}

# 每种检查类型要求的字段（id/type/severity/fix 为公共必填）
REQUIRED_FIELDS = {
    "file_exists": ["path"],
    "checksum": ["path", "algorithm", "expected"],
    "version_format": ["path", "pattern"],
    "metadata_field": ["path", "field"],
    "artifact_consistency": ["manifest"],
}


class ConfigError(Exception):
    """配置非法，运行前抛出。"""


class Failure:
    def __init__(self, check_id, severity, file, field, expected, actual, fix):
        self.check_id = check_id
        self.severity = severity
        self.file = file
        self.field = field
        self.expected = expected
        self.actual = actual
        self.fix = fix

    def to_dict(self):
        return {
            "check_id": self.check_id,
            "severity": self.severity,
            "file": self.file,
            "field": self.field,
            "expected": self.expected,
            "actual": self.actual,
            "fix": self.fix,
        }


# ---------------------------------------------------------------- 配置校验

def validate_config(config):
    """运行前校验配置；任何非法项都抛 ConfigError，避免假通过。"""
    errors = []
    if not isinstance(config, dict):
        raise ConfigError("配置顶层必须是 JSON 对象")
    checks = config.get("checks")
    if not isinstance(checks, list) or not checks:
        raise ConfigError("配置必须包含非空的 'checks' 数组（检查项缺失，拒绝运行）")

    seen_ids = set()
    for idx, check in enumerate(checks):
        where = "checks[%d]" % idx
        if not isinstance(check, dict):
            errors.append("%s: 检查项必须是对象" % where)
            continue
        check_id = check.get("id")
        if not check_id:
            errors.append("%s: 缺少必填字段 'id'" % where)
        elif check_id in seen_ids:
            errors.append("%s: 检查项 id '%s' 重复" % (where, check_id))
        else:
            seen_ids.add(check_id)
        if check_id:
            where = "check '%s'" % check_id

        ctype = check.get("type")
        if ctype not in CHECK_TYPES:
            errors.append("%s: 未知类型 '%s'（支持: %s）"
                          % (where, ctype, ", ".join(sorted(CHECK_TYPES))))
            continue
        severity = check.get("severity")
        if severity not in SEVERITY_RANK:
            errors.append("%s: severity 必须是 %s 之一，实际为 '%s'"
                          % (where, "/".join(SEVERITIES), severity))
        if not check.get("fix"):
            errors.append("%s: 缺少必填字段 'fix'（修复建议）" % where)
        for field_name in REQUIRED_FIELDS[ctype]:
            if field_name not in check:
                errors.append("%s: 类型 '%s' 缺少必填字段 '%s'"
                              % (where, ctype, field_name))
        if ctype == "checksum" and check.get("algorithm") not in HASH_ALGORITHMS:
            errors.append("%s: algorithm 必须是 %s 之一"
                          % (where, "/".join(sorted(HASH_ALGORITHMS))))
        if ctype == "version_format" and "pattern" in check:
            try:
                re.compile(check["pattern"])
            except re.error as exc:
                errors.append("%s: pattern 不是合法正则: %s" % (where, exc))
    if errors:
        raise ConfigError("配置非法，共 %d 处问题：\n  - %s"
                          % (len(errors), "\n  - ".join(errors)))


# ---------------------------------------------------------------- 检查执行

def hash_file(path, algorithm):
    """流式读取真实产物计算校验值（不依赖声明文件）。"""
    digest = hashlib.new(algorithm)
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_check(check, base_dir):
    """执行单个检查项，返回 Failure 列表（空列表表示通过）。"""
    ctype = check["type"]
    cid = check["id"]
    sev = check["severity"]
    fix = check["fix"]

    def fail(file, field, expected, actual):
        return Failure(cid, sev, file, field, expected, actual, fix)

    if ctype == "file_exists":
        path = os.path.join(base_dir, check["path"])
        if not os.path.isfile(path):
            return [fail(check["path"], "<existence>", "文件存在", "文件不存在")]
        return []

    if ctype == "checksum":
        path = os.path.join(base_dir, check["path"])
        if not os.path.isfile(path):
            return [fail(check["path"], "<existence>", "文件存在", "文件不存在")]
        actual = hash_file(path, check["algorithm"])
        if actual.lower() != check["expected"].lower():
            return [fail(check["path"], check["algorithm"],
                         check["expected"], actual)]
        return []

    if ctype == "version_format":
        path = os.path.join(base_dir, check["path"])
        if not os.path.isfile(path):
            return [fail(check["path"], "<existence>", "文件存在", "文件不存在")]
        with open(path, "r", encoding="utf-8") as fh:
            content = fh.read().strip()
        if not re.fullmatch(check["pattern"], content):
            return [fail(check["path"], "<content>",
                         "匹配正则 %s" % check["pattern"], repr(content))]
        return []

    if ctype == "metadata_field":
        path = os.path.join(base_dir, check["path"])
        if not os.path.isfile(path):
            return [fail(check["path"], "<existence>", "文件存在", "文件不存在")]
        try:
            with open(path, "r", encoding="utf-8") as fh:
                metadata = json.load(fh)
        except json.JSONDecodeError as exc:
            return [fail(check["path"], "<json>", "合法 JSON", "解析失败: %s" % exc)]
        node = metadata
        for key in check["field"].split("."):
            if isinstance(node, dict) and key in node:
                node = node[key]
            else:
                return [fail(check["path"], check["field"],
                             "字段存在", "字段缺失")]
        if "expected" in check and node != check["expected"]:
            return [fail(check["path"], check["field"],
                         repr(check["expected"]), repr(node))]
        return []

    if ctype == "artifact_consistency":
        manifest_path = os.path.join(base_dir, check["manifest"])
        if not os.path.isfile(manifest_path):
            return [fail(check["manifest"], "<existence>", "文件存在", "文件不存在")]
        try:
            with open(manifest_path, "r", encoding="utf-8") as fh:
                manifest = json.load(fh)
        except json.JSONDecodeError as exc:
            return [fail(check["manifest"], "<json>", "合法 JSON", "解析失败: %s" % exc)]
        declared = manifest.get("artifacts")
        if not isinstance(declared, list) or not declared:
            return [fail(check["manifest"], "artifacts",
                         "非空产物声明数组", "缺失或为空")]
        failures = []
        for entry in declared:
            rel = entry.get("path", "<unknown>")
            artifact_path = os.path.join(base_dir, rel)
            if not os.path.isfile(artifact_path):
                failures.append(fail(rel, "<existence>", "文件存在", "文件不存在"))
                continue
            declared_hash = entry.get("sha256")
            if not declared_hash:
                failures.append(fail(rel, "sha256", "声明中包含 sha256", "缺失"))
                continue
            # 关键：读取真实产物重新计算，而非只看声明
            actual_hash = hash_file(artifact_path, "sha256")
            if actual_hash.lower() != declared_hash.lower():
                failures.append(fail(rel, "sha256", declared_hash, actual_hash))
        return failures

    raise AssertionError("unreachable: %s" % ctype)  # validate_config 已拦截


# ---------------------------------------------------------------- 报告

def run_all(config):
    base_dir = config.get("base_dir", ".")
    failures = []
    started = time.monotonic()
    for check in config["checks"]:
        failures.extend(run_check(check, base_dir))
    elapsed = time.monotonic() - started
    failures.sort(key=lambda f: (SEVERITY_RANK[f.severity], f.check_id))
    return failures, len(config["checks"]), elapsed


def format_report(failures, total, elapsed):
    lines = []
    lines.append("=" * 64)
    lines.append("发布前检查报告  (共 %d 项检查，耗时 %.3fs)" % (total, elapsed))
    lines.append("=" * 64)
    if not failures:
        lines.append("结果: 全部通过 ✔")
        return "\n".join(lines)
    lines.append("结果: %d 项失败 ✘（按严重级别排序）" % len(failures))
    for idx, f in enumerate(failures, 1):
        lines.append("")
        lines.append("[%d] [%s] 检查项: %s" % (idx, f.severity.upper(), f.check_id))
        lines.append("    文件:   %s" % f.file)
        lines.append("    字段:   %s" % f.field)
        lines.append("    期望:   %s" % f.expected)
        lines.append("    实际:   %s" % f.actual)
        lines.append("    修复:   %s" % f.fix)
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="发布前检查清单自动化")
    parser.add_argument("--config", required=True, help="检查项配置 JSON")
    parser.add_argument("--json", dest="json_out", help="将失败报告同时写为 JSON")
    args = parser.parse_args(argv)

    try:
        with open(args.config, "r", encoding="utf-8") as fh:
            config = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print("配置加载失败: %s" % exc, file=sys.stderr)
        return 2
    try:
        validate_config(config)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    failures, total, elapsed = run_all(config)
    print(format_report(failures, total, elapsed))
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump({
                "total_checks": total,
                "elapsed_seconds": round(elapsed, 6),
                "passed": not failures,
                "failures": [f.to_dict() for f in failures],
            }, fh, ensure_ascii=False, indent=2)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
