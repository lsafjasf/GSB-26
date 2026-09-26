#!/usr/bin/env python3
"""release_check.py — 发布前检查清单自动化工具（仅标准库）。

用法:
    python3 release_check.py --config checks.example.json
    python3 release_check.py --config checks.example.json --json report.json
    python3 release_check.py --benchmark --bench-files 100 --bench-size-mb 5

退出码: 0 = 全部通过; 1 = 存在检查失败; 2 = 配置非法/运行前错误。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass

SEVERITIES = ("critical", "major", "minor")
SEVERITY_RANK = {name: idx for idx, name in enumerate(SEVERITIES)}
CHECK_TYPES = ("file_exists", "checksum", "version_format", "metadata_field", "manifest_consistency")
SEMVER_RE = r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$"
CHUNK = 1024 * 1024

EXIT_OK, EXIT_FAILED, EXIT_CONFIG_ERROR = 0, 1, 2


class ConfigError(Exception):
    """配置非法，运行前即报错。"""


@dataclass
class Failure:
    check_id: str
    severity: str
    file: str
    field: str
    expected: str
    actual: str
    fix: str

    def render(self) -> str:
        return (
            f"[{self.severity.upper()}] {self.check_id}\n"
            f"  file:     {self.file}\n"
            f"  field:    {self.field}\n"
            f"  expected: {self.expected}\n"
            f"  actual:   {self.actual}\n"
            f"  fix:      {self.fix}"
        )


# ---------------------------------------------------------------- 配置加载与校验

def load_config(path: str) -> dict:
    if not os.path.isfile(path):
        raise ConfigError(f"配置文件不存在: {path}")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"配置文件不是合法 JSON: {path}: {exc}") from exc
    validate_config(cfg)
    return cfg


def _require(check: dict, key: str, ctx: str) -> None:
    if key not in check:
        raise ConfigError(f"{ctx}: 缺少必填键 '{key}'")


def validate_config(cfg: dict) -> None:
    if not isinstance(cfg, dict):
        raise ConfigError("配置顶层必须是 JSON 对象")
    checks = cfg.get("checks")
    if not isinstance(checks, list) or not checks:
        raise ConfigError("配置必须包含非空 'checks' 数组（缺失检查项会导致假通过，已拒绝运行）")

    seen_ids = set()
    for i, check in enumerate(checks):
        ctx = f"checks[{i}]"
        if not isinstance(check, dict):
            raise ConfigError(f"{ctx}: 检查项必须是对象")
        _require(check, "id", ctx)
        _require(check, "type", ctx)
        _require(check, "severity", ctx)
        _require(check, "fix", ctx)
        cid, ctype = check["id"], check["type"]
        ctx = f"checks[{i}] (id={cid!r})"

        if not isinstance(cid, str) or not cid:
            raise ConfigError(f"{ctx}: 'id' 必须是非空字符串")
        if cid in seen_ids:
            raise ConfigError(f"{ctx}: 检查项 id 重复")
        seen_ids.add(cid)
        if ctype not in CHECK_TYPES:
            raise ConfigError(f"{ctx}: 未知检查类型 {ctype!r}，支持: {', '.join(CHECK_TYPES)}")
        if check["severity"] not in SEVERITIES:
            raise ConfigError(f"{ctx}: severity 必须是 {SEVERITIES} 之一")
        if not isinstance(check["fix"], str) or not check["fix"].strip():
            raise ConfigError(f"{ctx}: 'fix' 必须是非空修复建议")

        if ctype == "file_exists":
            _require(check, "path", ctx)
        elif ctype == "checksum":
            _require(check, "path", ctx)
            algo = check.get("algo", "sha256")
            if algo not in hashlib.algorithms_available:
                raise ConfigError(f"{ctx}: 不支持的摘要算法 {algo!r}")
            has_expected = "expected" in check
            has_from = "expected_from" in check
            if has_expected == has_from:
                raise ConfigError(f"{ctx}: 必须且只能提供 'expected' 或 'expected_from' 之一")
            if has_from:
                src = check["expected_from"]
                if not isinstance(src, dict) or "file" not in src or "field" not in src:
                    raise ConfigError(f"{ctx}: 'expected_from' 必须包含 'file' 与 'field'")
        elif ctype == "version_format":
            _require(check, "file", ctx)
            _require(check, "field", ctx)
            pattern = check.get("pattern", "semver")
            if pattern != "semver":
                try:
                    re.compile(pattern)
                except re.error as exc:
                    raise ConfigError(f"{ctx}: 非法正则 pattern: {exc}") from exc
        elif ctype == "metadata_field":
            _require(check, "file", ctx)
            _require(check, "field", ctx)
            if ("equals" in check) == ("pattern" in check):
                raise ConfigError(f"{ctx}: 必须且只能提供 'equals' 或 'pattern' 之一")
        elif ctype == "manifest_consistency":
            _require(check, "manifest", ctx)


# ---------------------------------------------------------------- 工具函数

def get_field(obj, dotted: str, ctx: str):
    """按 'a.b.0.c' 形式取值，数字段视为列表下标。"""
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, list):
            if not part.isdigit() or int(part) >= len(cur):
                raise ConfigError(f"{ctx}: 字段路径 '{dotted}' 在 '{part}' 处越界")
            cur = cur[int(part)]
        elif isinstance(cur, dict):
            if part not in cur:
                raise ConfigError(f"{ctx}: 字段路径 '{dotted}' 缺少键 '{part}'")
            cur = cur[part]
        else:
            raise ConfigError(f"{ctx}: 字段路径 '{dotted}' 在 '{part}' 处遇到非容器值")
    return cur


def load_json_file(path: str, ctx: str):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise ConfigError(f"{ctx}: 引用的 JSON 文件不存在: {path}")
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{ctx}: 引用的文件不是合法 JSON: {path}: {exc}")


def file_digest(path: str, algo: str) -> str:
    h = hashlib.new(algo)
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- 各类检查

def check_file_exists(check: dict, base: str) -> list[Failure]:
    path = os.path.join(base, check["path"])
    if os.path.isfile(path):
        return []
    return [Failure(check["id"], check["severity"], check["path"], "<exists>",
                    "文件存在", "文件缺失", check["fix"])]


def check_checksum(check: dict, base: str) -> list[Failure]:
    path = os.path.join(base, check["path"])
    algo = check.get("algo", "sha256")
    if "expected" in check:
        expected = str(check["expected"])
        expect_src = "配置中的 expected"
    else:
        src = check["expected_from"]
        src_file = os.path.join(base, src["file"])
        expected = str(get_field(load_json_file(src_file, check["id"]), src["field"], check["id"]))
        expect_src = f"{src['file']}:{src['field']}"
    if not os.path.isfile(path):
        return [Failure(check["id"], check["severity"], check["path"], f"<{algo}>",
                        f"{expected} (来自 {expect_src})", "文件缺失，无法计算校验值", check["fix"])]
    actual = file_digest(path, algo)
    if actual.lower() == expected.lower():
        return []
    return [Failure(check["id"], check["severity"], check["path"], f"<{algo}>",
                    f"{expected} (来自 {expect_src})", actual, check["fix"])]


def check_version_format(check: dict, base: str) -> list[Failure]:
    path = os.path.join(base, check["file"])
    pattern = check.get("pattern", "semver")
    regex = SEMVER_RE if pattern == "semver" else pattern
    value = get_field(load_json_file(path, check["id"]), check["field"], check["id"])
    if re.match(regex, str(value)):
        return []
    return [Failure(check["id"], check["severity"], check["file"], check["field"],
                    f"匹配 {pattern if pattern == 'semver' else regex}", str(value), check["fix"])]


def check_metadata_field(check: dict, base: str) -> list[Failure]:
    path = os.path.join(base, check["file"])
    value = get_field(load_json_file(path, check["id"]), check["field"], check["id"])
    if "equals" in check:
        if value == check["equals"]:
            return []
        return [Failure(check["id"], check["severity"], check["file"], check["field"],
                        json.dumps(check["equals"], ensure_ascii=False),
                        json.dumps(value, ensure_ascii=False), check["fix"])]
    if re.match(check["pattern"], str(value)):
        return []
    return [Failure(check["id"], check["severity"], check["file"], check["field"],
                    f"匹配 /{check['pattern']}/", str(value), check["fix"])]


def check_manifest_consistency(check: dict, base: str) -> list[Failure]:
    """产物与声明一致性：读取真实产物重新计算校验值/大小，与清单声明逐项比对。"""
    manifest_rel = check["manifest"]
    manifest = load_json_file(os.path.join(base, manifest_rel), check["id"])
    artifacts_field = check.get("artifacts_field", "artifacts")
    artifacts = get_field(manifest, artifacts_field, check["id"])
    if not isinstance(artifacts, list):
        raise ConfigError(f"{check['id']}: 字段 '{artifacts_field}' 必须是数组")

    failures: list[Failure] = []
    for idx, entry in enumerate(artifacts):
        if not isinstance(entry, dict) or "path" not in entry:
            raise ConfigError(f"{check['id']}: {artifacts_field}[{idx}] 缺少 'path'")
        rel = entry["path"]
        full = os.path.join(base, rel)
        prefix = f"{artifacts_field}[{idx}]"
        if not os.path.isfile(full):
            failures.append(Failure(check["id"], check["severity"], rel, f"{prefix}.path",
                                    "声明的产物存在于磁盘", "文件缺失", check["fix"]))
            continue
        if "sha256" in entry:
            actual = file_digest(full, "sha256")
            if actual.lower() != str(entry["sha256"]).lower():
                failures.append(Failure(check["id"], check["severity"], rel, f"{prefix}.sha256",
                                        str(entry["sha256"]), actual, check["fix"]))
        if "size" in entry:
            actual_size = os.path.getsize(full)
            if actual_size != entry["size"]:
                failures.append(Failure(check["id"], check["severity"], rel, f"{prefix}.size",
                                        str(entry["size"]), str(actual_size), check["fix"]))
    return failures


RUNNERS = {
    "file_exists": check_file_exists,
    "checksum": check_checksum,
    "version_format": check_version_format,
    "metadata_field": check_metadata_field,
    "manifest_consistency": check_manifest_consistency,
}


def run_checks(cfg: dict, config_dir: str) -> tuple[list[Failure], int]:
    base = cfg.get("base_dir", ".")
    if not os.path.isabs(base):
        base = os.path.normpath(os.path.join(config_dir, base))
    failures: list[Failure] = []
    for check in cfg["checks"]:
        failures.extend(RUNNERS[check["type"]](check, base))
    failures.sort(key=lambda f: (SEVERITY_RANK[f.severity], f.check_id, f.file, f.field))
    return failures, len(cfg["checks"])


# ---------------------------------------------------------------- 报告

def render_report(failures: list[Failure], total: int) -> str:
    lines = ["=" * 60, "RELEASE CHECK REPORT", "=" * 60]
    lines.append(f"checks: {total}  passed: {total - len({f.check_id for f in failures})}"
                 f"  failed-checks: {len({f.check_id for f in failures})}  failures: {len(failures)}")
    if not failures:
        lines.append("RESULT: PASS — 全部检查通过，可以发布。")
        return "\n".join(lines)
    lines.append(f"RESULT: FAIL — {len(failures)} 处失败（已按严重级别排序）")
    lines.append("-" * 60)
    for i, f in enumerate(failures, 1):
        lines.append(f"{i}. {f.render()}")
    return "\n".join(lines)


def failures_to_json(failures: list[Failure], total: int) -> str:
    return json.dumps({
        "result": "PASS" if not failures else "FAIL",
        "checks_total": total,
        "failures_total": len(failures),
        "failures": [vars(f) for f in failures],
    }, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- 基准测试

def run_benchmark(num_files: int, size_mb: int) -> int:
    tmp = tempfile.mkdtemp(prefix="release_check_bench_")
    artifacts = []
    payload = os.urandom(1024 * 1024)
    t0 = time.perf_counter()
    for i in range(num_files):
        rel = f"artifacts/blob_{i:04d}.bin"
        full = os.path.join(tmp, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        h = hashlib.sha256()
        size = 0
        with open(full, "wb") as fh:
            for _ in range(size_mb):
                fh.write(payload)
                h.update(payload)
                size += len(payload)
        artifacts.append({"path": rel, "sha256": h.hexdigest(), "size": size})
    with open(os.path.join(tmp, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"artifacts": artifacts}, fh)
    cfg = {"base_dir": tmp, "checks": [
        {"id": "consistency", "type": "manifest_consistency", "manifest": "manifest.json",
         "severity": "critical", "fix": "重新生成产物与清单"},
    ]}
    gen_s = time.perf_counter() - t0

    t1 = time.perf_counter()
    failures, total = run_checks(cfg, config_dir=tmp)
    run_s = time.perf_counter() - t1
    total_mb = num_files * size_mb
    print(f"基准: {num_files} 个产物 x {size_mb} MiB = {total_mb} MiB")
    print(f"生成夹具耗时: {gen_s:.2f}s（不计入检查耗时）")
    print(f"检查耗时: {run_s:.3f}s（{total} 项检查，{len(artifacts)} 个产物全量 sha256 校验）")
    print(f"吞吐: {total_mb / run_s:.0f} MiB/s, 平均每产物 {run_s / num_files * 1000:.1f} ms")
    print(f"结果: {'PASS' if not failures else 'FAIL'}")
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    return EXIT_OK if not failures else EXIT_FAILED


# ---------------------------------------------------------------- 入口

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="发布前检查清单自动化工具")
    ap.add_argument("--config", help="检查项配置 JSON 路径")
    ap.add_argument("--json", dest="json_out", help="将失败报告同时写入 JSON 文件")
    ap.add_argument("--benchmark", action="store_true", help="运行大规模产物性能基准")
    ap.add_argument("--bench-files", type=int, default=100)
    ap.add_argument("--bench-size-mb", type=int, default=5)
    args = ap.parse_args(argv)

    if args.benchmark:
        return run_benchmark(args.bench_files, args.bench_size_mb)
    if not args.config:
        ap.error("必须提供 --config（或使用 --benchmark）")

    try:
        cfg = load_config(args.config)
        failures, total = run_checks(cfg, os.path.dirname(os.path.abspath(args.config)))
    except ConfigError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    print(render_report(failures, total))
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            fh.write(failures_to_json(failures, total))
    return EXIT_OK if not failures else EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
