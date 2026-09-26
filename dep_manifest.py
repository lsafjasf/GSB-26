#!/usr/bin/env python3
"""构建产物依赖清单生成工具（仅使用 Python 标准库）。

从依赖声明（直接依赖）与锁文件（完整依赖图）出发：
  1. 计算含传递依赖的完整依赖集合，记录名称 / 版本 / 来源 / 全部引入路径；
  2. 与实际产物清单核对，报告两类差异（声明有而产物缺、产物有而清单未覆盖）；
  3. 元数据缺失（无版本号、来源不明、未入锁文件）的组件标记为待人工确认；
  4. 输出完全可复现：排序固定、无时间戳，同一输入多次生成字节级一致。

输入格式
--------
依赖声明 deps.json:
  {"direct": ["app-server", {"name": "legacy-lib", "version": "0.9"}]}
锁文件 lock.json:
  {"packages": [
    {"name": "app-server", "version": "2.1.0", "source": "registry:internal",
     "dependencies": ["http-lib", "json-lib"]},
    ...
  ]}
产物清单 artifact.txt（每行一个组件，name 或 name@version）:
  app-server@2.1.0
  http-lib
"""

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

TOOL_NAME = "dep-manifest"
TOOL_VERSION = "1.0.0"

REVIEW_MISSING_VERSION = "missing_version"   # 无版本号
REVIEW_UNKNOWN_SOURCE = "unknown_source"     # 来源不明
REVIEW_NOT_IN_LOCK = "not_in_lockfile"       # 声明了但锁文件中没有


# ---------------------------------------------------------------- 输入解析

def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_direct(entry):
    """直接依赖条目允许是字符串或对象，统一为 dict。"""
    if isinstance(entry, str):
        return {"name": entry}
    if isinstance(entry, dict) and "name" in entry:
        return dict(entry)
    raise ValueError(f"非法的直接依赖条目: {entry!r}")


def parse_artifact(text):
    """解析产物清单文本，返回 {name: version_or_None}。重复行去重。"""
    components = {}
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "@" in line:
            name, _, version = line.rpartition("@")
            name, version = name.strip(), version.strip()
            if not name:
                raise ValueError(f"产物清单第 {lineno} 行非法: {raw!r}")
            components[name] = version or None
        else:
            components[line] = None
    return components


# ---------------------------------------------------------------- 核心计算

def enumerate_paths(direct_names, packages):
    """枚举每个组件被引入的全部路径（根直接依赖 -> ... -> 组件）。

    返回 {name: set(tuple(paths))}。路径内不重复访问节点以避免环。
    """
    paths = {}
    stack = [(name, (name,)) for name in reversed(direct_names)]
    while stack:
        name, path = stack.pop()
        seen = paths.setdefault(name, set())
        if path in seen:
            continue
        seen.add(path)
        pkg = packages.get(name)
        if pkg is None:
            continue
        for child in pkg.get("dependencies", []):
            if child in path:  # 环保护
                continue
            stack.append((child, path + (child,)))
    return paths


def build_manifest(deps_doc, lock_doc):
    """根据依赖声明与锁文件构建清单（不读文件，便于测试）。"""
    packages = {}
    for entry in lock_doc.get("packages", []):
        name = entry.get("name")
        if not name:
            raise ValueError(f"锁文件条目缺少 name: {entry!r}")
        packages[name] = entry

    direct_entries = [normalize_direct(e) for e in deps_doc.get("direct", [])]
    direct_names = [e["name"] for e in direct_entries]
    if len(set(direct_names)) != len(direct_names):
        raise ValueError("依赖声明中存在重复的直接依赖")

    all_paths = enumerate_paths(direct_names, packages)

    components = []
    for name in sorted(all_paths):
        pkg = packages.get(name)
        decl = next((e for e in direct_entries if e["name"] == name), None)

        version = None
        source = None
        reasons = []
        if pkg is not None:
            version = pkg.get("version") or None
            source = pkg.get("source") or None
        else:
            # 锁文件缺失：只能采用声明中的信息，绝不猜测
            reasons.append(REVIEW_NOT_IN_LOCK)
            if decl:
                version = decl.get("version") or None
                source = decl.get("source") or None
        if version is None:
            reasons.append(REVIEW_MISSING_VERSION)
        if source is None:
            reasons.append(REVIEW_UNKNOWN_SOURCE)

        components.append({
            "name": name,
            "version": version,
            "source": source,
            "direct": decl is not None,
            "introduced_by": sorted(list(p) for p in all_paths[name]),
            "needs_review": bool(reasons),
            "review_reasons": sorted(reasons),
        })

    return {
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "component_count": len(components),
        "components": components,
    }


def diff_report(manifest, artifact_components):
    """对比清单与产物，返回差异报告（不读文件，便于测试）。"""
    declared = {c["name"]: c for c in manifest["components"]}
    declared_names = set(declared)
    artifact_names = set(artifact_components)

    missing_in_artifact = []
    for name in sorted(declared_names - artifact_names):
        comp = declared[name]
        missing_in_artifact.append({
            "name": name,
            "version": comp["version"],
            "source": comp["source"],
            "introduced_by": comp["introduced_by"],
        })

    uncovered_in_artifact = []
    for name in sorted(artifact_names - declared_names):
        uncovered_in_artifact.append({
            "name": name,
            "version": artifact_components[name],
        })

    version_mismatches = []
    for name in sorted(declared_names & artifact_names):
        declared_version = declared[name]["version"]
        artifact_version = artifact_components[name]
        if declared_version and artifact_version and declared_version != artifact_version:
            version_mismatches.append({
                "name": name,
                "declared_version": declared_version,
                "artifact_version": artifact_version,
            })

    return {
        "summary": {
            "declared": len(declared_names),
            "in_artifact": len(artifact_names),
            "missing_in_artifact": len(missing_in_artifact),
            "uncovered_in_artifact": len(uncovered_in_artifact),
            "version_mismatches": len(version_mismatches),
            "consistent": not (missing_in_artifact or uncovered_in_artifact
                               or version_mismatches),
        },
        "missing_in_artifact": missing_in_artifact,
        "uncovered_in_artifact": uncovered_in_artifact,
        "version_mismatches": version_mismatches,
    }


# ---------------------------------------------------------------- 输出

def dump_json(doc):
    """确定性序列化：键排序、缩进固定、末尾换行，不含任何时间戳。"""
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_text(path, text):
    Path(path).write_text(text, encoding="utf-8")


# ---------------------------------------------------------------- CLI

def cmd_generate(args):
    started = time.perf_counter()
    deps_doc = load_json(args.deps)
    lock_doc = load_json(args.lock)
    manifest = build_manifest(deps_doc, lock_doc)
    manifest["input_fingerprints"] = {
        "deps_sha256": sha256_file(args.deps),
        "lock_sha256": sha256_file(args.lock),
    }
    write_text(args.output, dump_json(manifest))
    elapsed = time.perf_counter() - started
    print(f"清单已生成: {args.output} "
          f"({manifest['component_count']} 个组件, 耗时 {elapsed:.3f}s)",
          file=sys.stderr)
    return 0


def cmd_check(args):
    started = time.perf_counter()
    manifest = load_json(args.manifest)
    artifact = parse_artifact(Path(args.artifact).read_text(encoding="utf-8"))
    report = diff_report(manifest, artifact)
    write_text(args.output, dump_json(report))
    elapsed = time.perf_counter() - started
    summary = report["summary"]
    print(f"差异报告已生成: {args.output} (耗时 {elapsed:.3f}s)", file=sys.stderr)
    print(f"  声明组件: {summary['declared']}, 产物组件: {summary['in_artifact']}",
          file=sys.stderr)
    print(f"  产物缺失: {summary['missing_in_artifact']}, "
          f"清单未覆盖: {summary['uncovered_in_artifact']}, "
          f"版本不一致: {summary['version_mismatches']}", file=sys.stderr)
    return 0 if summary["consistent"] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="dep_manifest",
        description="构建产物依赖清单生成与核对工具（仅标准库）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_gen = sub.add_parser("generate", help="由依赖声明 + 锁文件生成清单")
    p_gen.add_argument("--deps", required=True, help="依赖声明 JSON")
    p_gen.add_argument("--lock", required=True, help="锁文件 JSON")
    p_gen.add_argument("-o", "--output", required=True, help="清单输出路径")
    p_gen.set_defaults(func=cmd_generate)

    p_chk = sub.add_parser("check", help="清单与产物核对，输出差异报告")
    p_chk.add_argument("--manifest", required=True, help="已生成的清单 JSON")
    p_chk.add_argument("--artifact", required=True, help="产物清单文本文件")
    p_chk.add_argument("-o", "--output", required=True, help="差异报告输出路径")
    p_chk.set_defaults(func=cmd_check)

    p_all = sub.add_parser("all", help="generate + check 一步完成")
    p_all.add_argument("--deps", required=True)
    p_all.add_argument("--lock", required=True)
    p_all.add_argument("--artifact", required=True)
    p_all.add_argument("--manifest-out", dest="output", required=True)
    p_all.add_argument("--report-out", dest="report", required=True)
    p_all.set_defaults(func=lambda a: _run_all(a))

    args = parser.parse_args(argv)
    return args.func(args)


def _run_all(args):
    gen_args = argparse.Namespace(deps=args.deps, lock=args.lock,
                                  output=args.output)
    cmd_generate(gen_args)
    chk_args = argparse.Namespace(manifest=args.output, artifact=args.artifact,
                                  output=args.report)
    return cmd_check(chk_args)


if __name__ == "__main__":
    sys.exit(main())
