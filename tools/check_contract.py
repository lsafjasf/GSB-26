"""错误码稳定性契约 / 对照表兼容性检查。

任何一项不通过即以非零码退出，适合接入 CI。检查分四组：

  A. 契约表自身合法（版本、冻结字段、分类枚举、错误码命名）
  B. 注册表 refactored/errors.py:_REGISTRY 与契约表严格一致：
     错误码集合相同（删除/改名/新增未登记 => 失败），
     category、retryable、required_context 逐一相同（改义 => 失败）
  C. 对照表产物新鲜且全绿：
     必须由 fault_matrix 以当前契约重新生成（手工漂移 => 失败），
     每一行 legacy/refactored 语义投影 MATCH，
     服务级内层 code 与模块级 code 一致（跨层漂移 => 失败）
  D. 跨模块稳定性：对照表中同一个 code 无论出现在哪个模块/层位，
     category 与 retryable 必须一致

用法: python3 tools/check_contract.py
"""

import json
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

from refactored.errors import _REGISTRY  # noqa: E402

CONTRACT_PATH = os.path.join(ROOT, "contracts", "error_codes.json")
MATRIX_JSON = os.path.join(ROOT, "contracts", "fault_matrix.json")
MATRIX_MD = os.path.join(ROOT, "contracts", "fault_matrix.md")

sys.path.insert(0, os.path.join(ROOT, "tools"))
import fault_matrix  # noqa: E402

CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")


class Report:
    def __init__(self):
        self.failures = []
        self.checks = 0

    def ok(self, msg):
        self.checks += 1
        print("  ok   %s" % msg)

    def bad(self, msg):
        self.checks += 1
        self.failures.append(msg)
        print("  FAIL %s" % msg)

    def section(self, title):
        print("\n[%s]" % title)


def template_placeholders(template):
    return set(re.findall(r"\{(\w+)\}", template or ""))


def check_contract_schema(rep, contract):
    rep.section("A. 契约表合法性")
    if contract.get("version", 0) < 1:
        rep.bad("contract.version 必须为 >=1 的整数")
    frozen = contract.get("frozen_fields")
    if frozen != ["code", "category", "retryable", "required_context"]:
        rep.bad("frozen_fields 被改动: %r" % (frozen,))
    else:
        rep.ok("frozen_fields 完整")
    allowed = set(contract.get("allowed_categories", []))
    codes = contract.get("codes", {})
    if not codes:
        rep.bad("codes 为空")
    for code, spec in codes.items():
        if not CODE_PATTERN.match(code):
            rep.bad("错误码命名不合法: %r" % code)
        cat = spec.get("category")
        if cat not in allowed:
            rep.bad("%s.category=%r 不在允许分类 %s 内"
                    % (code, cat, sorted(allowed)))
        if not isinstance(spec.get("retryable"), bool):
            rep.bad("%s.retryable 必须是布尔值" % code)
        ctx = spec.get("required_context")
        if not isinstance(ctx, list) or not ctx or ctx != sorted(ctx) \
                or len(ctx) != len(set(ctx)):
            rep.bad("%s.required_context 必须是非空、去重、排序的列表" % code)
        missing = template_placeholders(spec.get("message_template")) - set(ctx)
        if missing:
            rep.bad("%s 模板占位符 %s 未全部纳入 required_context"
                    % (code, sorted(missing)))
    if rep.failures:
        return
    rep.ok("%d 个错误码命名/分类/可重试/上下文字段均合法" % len(codes))


def check_registry_vs_contract(rep, contract):
    rep.section("B. 注册表 ↔ 契约一致性（删码/改义即失败）")
    contract_codes = set(contract["codes"])
    registry_codes = set(_REGISTRY)

    deleted = contract_codes - registry_codes
    added = registry_codes - contract_codes
    for code in sorted(deleted):
        rep.bad("错误码 %s 已从注册表删除/改名（契约要求保留）" % code)
    for code in sorted(added):
        rep.bad("错误码 %s 未登记进契约即使用（新增必须先入契约）" % code)
    if not deleted and not added:
        rep.ok("错误码集合严格一致（%d 个，无删除/改名/私增）"
               % len(registry_codes))

    for code in sorted(contract_codes & registry_codes):
        spec = contract["codes"][code]
        actual = _REGISTRY[code]
        if actual.category.value != spec["category"]:
            rep.bad("%s.category 漂移: 契约=%s 注册表=%s"
                    % (code, spec["category"], actual.category.value))
        if actual.retryable != spec["retryable"]:
            rep.bad("%s.retryable 漂移: 契约=%s 注册表=%s"
                    % (code, spec["retryable"], actual.retryable))
        placeholders = template_placeholders(actual.message_template)
        if placeholders != set(spec["required_context"]):
            rep.bad("%s.required_context 漂移: 契约=%s 注册表模板字段=%s"
                    % (code, spec["required_context"], sorted(placeholders)))
    if not deleted and not added and not [
            f for f in rep.failures if "漂移" in f]:
        rep.ok("全部错误码 category/retryable/required_context 未漂移")


def check_matrix(rep, contract):
    rep.section("C. 对照表产物新鲜度与逐行 MATCH")
    try:
        with open(MATRIX_JSON, encoding="utf-8") as fh:
            artifact = json.load(fh)
    except FileNotFoundError:
        rep.bad("缺少 %s，请先运行 %s"
                % (os.path.relpath(MATRIX_JSON, ROOT),
                   fault_matrix.GEN_CMD))
        return
    except json.JSONDecodeError as exc:
        rep.bad("对照表 JSON 无法解析: %s" % exc)
        return

    expected = fault_matrix.build_artifacts(contract,
                                            fault_matrix.build_rows(contract))
    if artifact != expected:
        rep.bad("对照表产物与当前代码/契约不一致（被手改或未重新生成）；"
                "请运行 %s" % fault_matrix.GEN_CMD)
    else:
        rep.ok("对照表由当前代码 + 当前契约新鲜生成，无手工漂移")

    if os.path.exists(MATRIX_MD):
        with open(MATRIX_MD, encoding="utf-8") as fh:
            md_text = fh.read()
        if md_text != fault_matrix.render_markdown(expected):
            rep.bad("Markdown 对照表与 JSON 产物不一致；请重新运行 %s"
                    % fault_matrix.GEN_CMD)
        else:
            rep.ok("Markdown 对照表与 JSON 产物一致")
    else:
        rep.bad("缺少 %s" % os.path.relpath(MATRIX_MD, ROOT))

    rows = artifact.get("rows", [])
    drifted = [r for r in rows if r.get("verdict") != "MATCH"]
    for row in drifted:
        rep.bad("对照行 %s/%s 语义不一致: legacy=%r refactored=%r"
                % (row.get("scenario"), row.get("layer"),
                   row.get("legacy"), row.get("refactored")))
    if rows and not drifted:
        rep.ok("%d 行对照全部 MATCH：同故障的 code/分类/可重试/关键上下文一致"
               % len(rows))

    by_scenario = {}
    for row in rows:
        by_scenario.setdefault(row["scenario"], {})[row["layer"]] = row
    for scenario in sorted(by_scenario):
        layers = by_scenario[scenario]
        module = layers.get("module")
        inner = layers.get("service_inner")
        if module and inner:
            module_code = module["refactored"]["code"]
            inner_code = inner["refactored"]["code"]
            if module_code != inner_code:
                rep.bad("%s 跨层漂移: 模块级=%s 服务内层=%s"
                        % (scenario, module_code, inner_code))
    if rows:
        rep.ok("服务级内层错误码与模块级一致，包装层未改写下游语义")


def check_cross_module_stability(rep, contract):
    rep.section("D. 跨模块/跨层位稳定性（同码必同义）")
    with open(MATRIX_JSON, encoding="utf-8") as fh:
        rows = json.load(fh)["rows"]
    seen = {}
    bad = []
    for row in rows:
        for side in ("legacy", "refactored"):
            view = row[side]
            if not view:
                continue
            sig = (view["category"], view["retryable"])
            origin = "%s/%s/%s" % (row["scenario"], row["layer"], side)
            if view["code"] in seen and seen[view["code"]][0] != sig:
                bad.append((view["code"], seen[view["code"]][1], origin,
                            seen[view["code"]][0], sig))
            else:
                seen.setdefault(view["code"], (sig, origin))
    for code, where_a, where_b, sig_a, sig_b in bad:
        rep.bad("%s 语义随模块漂移: %s=%s vs %s=%s"
                % (code, where_a, sig_a, where_b, sig_b))
    if not bad:
        rep.ok("%d 个错误码在全部模块/层位/两侧的分类与可重试标记一致"
               % len(seen))


def main():
    with open(CONTRACT_PATH, encoding="utf-8") as fh:
        contract = json.load(fh)
    rep = Report()
    check_contract_schema(rep, contract)
    check_registry_vs_contract(rep, contract)
    check_matrix(rep, contract)
    check_cross_module_stability(rep, contract)

    print("\n%d 项检查，%d 项失败" % (rep.checks, len(rep.failures)))
    if rep.failures:
        print("契约检查未通过。")
        return 1
    print("错误码稳定性契约检查通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
