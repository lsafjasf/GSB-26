"""覆盖清单、差异报告与兼容性判定的渲染（Markdown / 控制台）。"""

from __future__ import annotations

from typing import Dict, List, Tuple

from contractfw import (
    ContractCase,
    CaseResult,
    Verdict,
    judge,
)

PASS_MARK = "PASS"
FAIL_MARK = "FAIL"


def failing_ids(results: List[CaseResult]) -> List[str]:
    return [r.case_id for r in results if not r.passed]


def coverage_rows(cases: List[ContractCase],
                  impl_results: Dict[str, List[CaseResult]]) -> List[Tuple[str, int, Dict[str, str]]]:
    """按契约点聚合：(point, case_count, {impl: PASS/FAIL})。

    一个契约点在某实现上只有其全部用例通过才记 PASS，否则 FAIL。
    """
    points: Dict[str, List[ContractCase]] = {}
    for case in cases:
        points.setdefault(case.point, []).append(case)

    rows = []
    for point, point_cases in points.items():
        cell = {}
        ids = {c.id for c in point_cases}
        for impl, results in impl_results.items():
            res_by_id = {r.case_id: r for r in results}
            cell[impl] = PASS_MARK if all(res_by_id[i].passed for i in ids) else FAIL_MARK
        rows.append((point, len(point_cases), cell))
    return rows


def uncovered_points(required_points: List[str],
                     cases: List[ContractCase]) -> List[str]:
    """要求覆盖但没有任何用例声明的契约点。"""
    covered = {c.point for c in cases}
    return [p for p in required_points if p not in covered]


def render_markdown(title: str,
                    cases: List[ContractCase],
                    impl_results: Dict[str, List[CaseResult]],
                    required_points: List[str]) -> Tuple[str, Dict[str, Verdict]]:
    impl_names = list(impl_results.keys())
    lines: List[str] = []
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"契约用例总数：{len(cases)}；被测实现：{', '.join(impl_names)}。")
    lines.append("")

    # 1. 契约覆盖清单
    lines.append("## 1. 契约覆盖清单")
    lines.append("")
    header = "| 契约点 | 用例数 | " + " | ".join(impl_names) + " |"
    sep = "|---|---:|" + "---|" * len(impl_names)
    lines.append(header)
    lines.append(sep)
    for point, count, cell in coverage_rows(cases, impl_results):
        lines.append(f"| {point} | {count} | " + " | ".join(cell[i] for i in impl_names) + " |")
    uncovered = uncovered_points(required_points, cases)
    if uncovered:
        for p in uncovered:
            lines.append(f"| {p} | 0 | " + " | ".join("未覆盖" for _ in impl_names) + " |")
    lines.append("")
    lines.append("说明：PASS = 该实现满足该契约点的全部用例；FAIL = 至少一条不一致；"
                 "“未覆盖” = 契约要求该点但无用例声明。")
    lines.append("")

    # 2. 差异报告
    lines.append("## 2. 差异报告（不一致明细）")
    lines.append("")
    cases_by_id = {c.id: c for c in cases}
    any_fail = False
    for impl, results in impl_results.items():
        fails = [r for r in results if not r.passed]
        if not fails:
            continue
        any_fail = True
        lines.append(f"### 实现：{impl}")
        lines.append("")
        lines.append("| 用例 | 契约点 | 严重程度 | 差异 |")
        lines.append("|---|---|---|---|")
        for r in fails:
            case = cases_by_id[r.case_id]
            detail = "<br>".join(r.failures)
            lines.append(f"| {case.id} | {case.point} | {case.severity} | {detail} |")
        lines.append("")
    if not any_fail:
        lines.append("所有实现均与契约一致，无差异。")
        lines.append("")

    # 3. 兼容性判定
    lines.append("## 3. 兼容性判定")
    lines.append("")
    lines.append("| 实现 | 结论 | 依据 |")
    lines.append("|---|---|---|")
    verdicts: Dict[str, Verdict] = {}
    for impl, results in impl_results.items():
        verdict = judge(results, cases)
        verdicts[impl] = verdict
        lines.append(f"| {impl} | **{verdict.level}** | {verdict.reason} |")
    lines.append("")
    lines.append("判定规则：全部通过为“可替换”；失败最高严重程度仅为 minor 为"
                 "“有条件替换”；存在 major/critical 失败为“不可替换”。")
    lines.append("")
    return "\n".join(lines), verdicts


def render_console(cases: List[ContractCase],
                   impl_results: Dict[str, List[CaseResult]]) -> str:
    cases_by_id = {c.id: c for c in cases}
    out = []
    for impl, results in impl_results.items():
        fails = [r for r in results if not r.passed]
        verdict = judge(results, cases)
        out.append(f"[{impl}] 用例 {len(results)} 条，通过 {len(results) - len(fails)} 条，"
                   f"失败 {len(fails)} 条 => {verdict.level}")
        for r in fails:
            case = cases_by_id[r.case_id]
            out.append(f"    - [{case.severity}] {case.id} ({case.point})")
            for f in r.failures:
                out.append(f"        * {f}")
    return "\n".join(out)
