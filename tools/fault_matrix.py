"""生成重构前后下游故障对照表（JSON + Markdown）。

同一组下游故障分别注入 legacy / refactored 实现，覆盖：
  - 模块级（net/store/cache 直接抛出）
  - 服务级（service.load_profile 包装后的外层错误）
  - 服务级内层（被包装的下游错误，验证包装不改变语义）

每行把两侧错误投影为 (code, category, retryable, 关键上下文) 并逐条裁定
MATCH/DRIFT。产物由 tools/check_contract.py 校验新鲜度，禁止手改。

用法:
  python3 tools/fault_matrix.py           # 重新生成产物
  python3 tools/fault_matrix.py --stdout  # 只打印 Markdown 对照表
"""

import argparse
import json
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

from legacy import cache as legacy_cache
from legacy import net as legacy_net
from legacy import service as legacy_service
from legacy import store as legacy_store
from refactored import cache as new_cache
from refactored import compat
from refactored import net as new_net
from refactored import service as new_service
from refactored import store as new_store
from refactored.errors import AppError

CONTRACT_PATH = os.path.join(ROOT, "contracts", "error_codes.json")
JSON_OUT = os.path.join(ROOT, "contracts", "fault_matrix.json")
MD_OUT = os.path.join(ROOT, "contracts", "fault_matrix.md")

GEN_CMD = "python3 tools/fault_matrix.py"
SCHEMA_VERSION = 1

# === 固定的一组下游故障（legacy 与 refactored 共用同一注入） ===
def boom_timeout(url, timeout):
    raise TimeoutError("timed out")


def boom_refused(url, timeout):
    raise ConnectionRefusedError(111, "Connection refused")


def boom_dial(dsn):
    raise ConnectionRefusedError(111, "Connection refused")


def boom_backend(key):
    raise OSError(113, "No route to host")


def legacy_deadlock_conn():
    class Conn:
        def execute(self, sql, params):
            raise legacy_store.DeadlockError("deadlock detected")
    return Conn()


def new_deadlock_conn():
    class Conn:
        def execute(self, sql, params):
            raise new_store.DeadlockError("deadlock detected")
    return Conn()


SQL = "UPDATE profile SET name=%s"

# 模块级故障场景：(行 id, 层位, legacy 调用, refactored 调用)
MODULE_SCENARIOS = [
    ("net_timeout", "module",
     lambda: legacy_net.http_get("http://api.internal/users/7",
                                 transport=boom_timeout, timeout=1.5),
     lambda: new_net.http_get("http://api.internal/users/7",
                              transport=boom_timeout, timeout=1.5)),
    ("net_conn_refused", "module",
     lambda: legacy_net.http_get("http://api.internal/users/7",
                                 transport=boom_refused),
     lambda: new_net.http_get("http://api.internal/users/7",
                              transport=boom_refused)),
    ("store_conn", "module",
     lambda: legacy_store.connect("db://main", dial=boom_dial),
     lambda: new_store.connect("db://main", dial=boom_dial)),
    ("store_query", "module",
     lambda: legacy_store.query(legacy_deadlock_conn(), SQL),
     lambda: new_store.query(new_deadlock_conn(), SQL)),
    ("cache_backend_down", "module",
     lambda: legacy_cache.get("profile:7", backend=boom_backend),
     lambda: new_cache.get("profile:7", backend=boom_backend)),
]

# 服务级故障场景：id 与模块级对齐，便于校验包装前后语义一致
SERVICE_SCENARIOS = [
    ("net_timeout", dict(transport=boom_timeout,
                         dial=lambda dsn: object(),
                         backend=lambda key: b"x")),
    ("net_conn_refused", dict(transport=boom_refused,
                              dial=lambda dsn: object(),
                              backend=lambda key: b"x")),
    ("store_conn", dict(transport=lambda url, t: b"{}",
                        dial=boom_dial,
                        backend=lambda key: b"x")),
    ("cache_backend_down", dict(transport=lambda url, t: b"{}",
                                dial=lambda dsn: object(),
                                backend=boom_backend)),
]
USER_ID = "42"

LAYER_LABELS = {
    "module": "模块级",
    "service_outer": "服务级·外层",
    "service_inner": "服务级·内层",
}


def load_contract():
    with open(CONTRACT_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def capture(fn):
    try:
        fn()
    except BaseException as exc:  # 两个实现都会抛，取首个异常
        return exc
    raise AssertionError("故障注入未产生异常")


def project(exc):
    """任一版本错误 -> 规范化投影 (code, category, retryable, 关键上下文)。"""
    err = exc if isinstance(exc, AppError) else compat.from_legacy(exc)
    if err is None:
        return None
    return {
        "code": err.code,
        "category": err.category.value,
        "retryable": err.retryable,
        "context": {k: err.context[k] for k in sorted(err.context)
                    if k in _REQUIRED.get(err.code, set())},
        "exc_type": type(exc).__name__,
    }


_REQUIRED = {}


def init_required(contract):
    for code, spec in contract["codes"].items():
        _REQUIRED[code] = set(spec["required_context"])


def make_row(scenario_id, layer, old_exc, new_exc):
    legacy_side = project(old_exc)
    refactored_side = project(new_exc)

    def semantic(view):
        return None if view is None else (
            view["code"], view["category"], view["retryable"],
            tuple(sorted(view["context"].items())))

    verdict = ("MATCH" if semantic(legacy_side) == semantic(refactored_side)
               else "DRIFT")
    return {
        "scenario": scenario_id,
        "layer": layer,
        "legacy": legacy_side,
        "refactored": refactored_side,
        "verdict": verdict,
    }


def build_rows(contract):
    init_required(contract)
    rows = []
    for scenario_id, _layer, old_fn, new_fn in MODULE_SCENARIOS:
        rows.append(make_row(scenario_id, "module",
                             capture(old_fn), capture(new_fn)))
    for scenario_id, deps in SERVICE_SCENARIOS:
        old_outer = capture(lambda d=deps:
                            legacy_service.load_profile(USER_ID, **d))
        new_outer = capture(lambda d=deps:
                            new_service.load_profile(USER_ID, **d))
        rows.append(make_row(scenario_id, "service_outer",
                             old_outer, new_outer))
        old_inner = compat.from_legacy(old_outer).__cause__
        new_inner = new_outer.__cause__
        rows.append(make_row(scenario_id, "service_inner",
                             old_inner, new_inner))
    rows.sort(key=lambda r: (r["scenario"], r["layer"]))
    return rows


def build_artifacts(contract, rows):
    data = {
        "schema_version": SCHEMA_VERSION,
        "generated_by": GEN_CMD,
        "contract_version": contract["version"],
        "note": "生成物，请勿手改；修改故障场景后重新运行生成命令。",
        "projection": ["code", "category", "retryable", "required_context"],
        "rows": rows,
    }
    return data


def render_markdown(data):
    lines = [
        "# 重构前后下游故障对照表",
        "",
        "- 生成命令：`%s`（生成物，请勿手改）" % GEN_CMD,
        "- 契约版本：`%s`；投影字段：code / category / retryable / 关键上下文"
        % data["contract_version"],
        "- 同一行左（legacy 经 `compat.from_legacy` 投影）右（refactored 原生）"
        "为同一次故障注入，逐字段可核对。",
        "",
        "| 故障场景 | 层位 | legacy 类型 | 旧 code | 旧分类 | 旧可重试 | "
        "新类型 | 新 code | 新分类 | 新可重试 | 关键上下文（两侧一致） | 裁定 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]

    def side(view):
        if view is None:
            return ("-", "-", "-", "-")
        return (view["exc_type"], view["code"], view["category"],
                str(view["retryable"]))

    for row in data["rows"]:
        old = side(row["legacy"])
        new = side(row["refactored"])
        ctx = ""
        if row["refactored"]:
            ctx = ", ".join("%s=%r" % kv for kv in
                            sorted(row["refactored"]["context"].items()))
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |"
                     % (row["scenario"], LAYER_LABELS[row["layer"]],
                        old[0], old[1], old[2], old[3],
                        new[0], new[1], new[2], new[3],
                        ctx or "-", row["verdict"]))

    total = len(data["rows"])
    matched = sum(1 for r in data["rows"] if r["verdict"] == "MATCH")
    lines += [
        "",
        "## 汇总",
        "",
        "- 对照行：%d；MATCH：%d；DRIFT：%d" %
        (total, matched, total - matched),
        "- 跨层稳定性：服务级内层 code 必须与模块级 code 完全一致"
        "（包装层只允许新增外层码，禁止改写下游语义）。",
        "",
        "| 故障场景 | 模块级 code | 服务级内层 code | 外层 code | 跨层裁定 |",
        "|---|---|---|---|---|",
    ]
    by_scenario = {}
    for row in data["rows"]:
        by_scenario.setdefault(row["scenario"], {})[row["layer"]] = row
    for scenario in sorted(by_scenario):
        layers = by_scenario[scenario]
        module_code = layers["module"]["refactored"]["code"]
        inner_row = layers.get("service_inner")
        outer_row = layers.get("service_outer")
        inner_code = inner_row["refactored"]["code"] if inner_row else "-"
        outer_code = outer_row["refactored"]["code"] if outer_row else "-"
        cross = "STABLE" if inner_code in ("-", module_code) else "DRIFT"
        lines.append("| %s | %s | %s | %s | %s |"
                     % (scenario, module_code, inner_code, outer_code, cross))
    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stdout", action="store_true",
                        help="只打印 Markdown，不写文件")
    args = parser.parse_args()

    contract = load_contract()
    rows = build_rows(contract)
    data = build_artifacts(contract, rows)
    markdown = render_markdown(data)

    if args.stdout:
        print(markdown)
        return
    with open(JSON_OUT, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")
    with open(MD_OUT, "w", encoding="utf-8") as fh:
        fh.write(markdown)
    matched = sum(1 for r in rows if r["verdict"] == "MATCH")
    print("wrote %s" % os.path.relpath(JSON_OUT, ROOT))
    print("wrote %s" % os.path.relpath(MD_OUT, ROOT))
    print("rows=%d MATCH=%d DRIFT=%d" %
          (len(rows), matched, len(rows) - matched))


if __name__ == "__main__":
    main()
