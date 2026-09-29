"""报告渲染：契约覆盖清单、差异报告、兼容性判定。"""
import unicodedata

from .core import behavior_diffs, judge

CHECK = "✓"
CROSS = "✗"


def _width(text):
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _pad(text, width):
    return text + " " * max(0, width - _width(text))


def _table(headers, rows):
    widths = [_width(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], _width(str(cell)))
    lines = ["  ".join(_pad(h, widths[i]) for i, h in enumerate(headers))]
    lines.append("  ".join("-" * w for w in widths))
    for row in rows:
        lines.append("  ".join(_pad(str(c), widths[i]) for i, c in enumerate(row)))
    return lines


def render_coverage(cases, results_by_impl, reference_name):
    """覆盖清单：每个契约点被哪些实现满足，以及整体覆盖状态。"""
    impl_names = list(results_by_impl.keys())
    headers = ["契约点", "分类", "严重度"] + impl_names + ["覆盖状态"]
    rows = []
    uncovered = []
    inconsistent = []
    for index, case in enumerate(cases):
        marks = []
        for name in impl_names:
            marks.append(CHECK if results_by_impl[name][index].ok else CROSS)
        ref_ok = results_by_impl[reference_name][index].ok
        if not ref_ok:
            status = "未覆盖（参考实现未通过）"
            uncovered.append(case["id"])
        elif len(set(marks)) > 1:
            status = "行为不一致"
            inconsistent.append(case["id"])
        else:
            status = "已覆盖"
        rows.append([case["id"], case.get("category", "-"),
                     case.get("severity", "major")] + marks + [status])
    lines = ["【契约覆盖清单】（参考实现：%s）" % reference_name]
    lines += _table(headers, rows)
    lines.append("小结：契约点 %d 个；未覆盖 %d 个%s；存在实现间不一致 %d 个%s。" % (
        len(cases),
        len(uncovered), ("：" + ", ".join(uncovered)) if uncovered else "",
        len(inconsistent), ("：" + ", ".join(inconsistent)) if inconsistent else ""))
    return lines


def render_failures(cases, results_by_impl):
    """各实现未满足契约断言的明细。"""
    lines = ["【契约符合性明细】"]
    any_failure = False
    for name, results in results_by_impl.items():
        for result in results:
            if result.ok:
                continue
            any_failure = True
            lines.append("  [%s] 用例 %s：" % (name, result.case["id"]))
            for failure in result.failures:
                lines.append("    - %s" % failure)
    if not any_failure:
        lines.append("  全部实现满足全部契约断言。")
    return lines


def render_diffs(cases, results_by_impl, reference_name):
    """差异报告：候选实现相对参考实现的行为不一致点。"""
    lines = ["【行为差异报告】（基准：%s）" % reference_name]
    diffs_by_impl = {}
    for name, results in results_by_impl.items():
        if name == reference_name:
            continue
        diffs = behavior_diffs(results_by_impl[reference_name], results)
        diffs_by_impl[name] = diffs
        if not diffs:
            lines.append("  [%s] 与参考实现行为完全一致。" % name)
            continue
        lines.append("  [%s] 发现 %d 处行为差异：" % (name, len(diffs)))
        for d in diffs:
            lines.append("    - 用例 %s（%s，%s）：" % (
                d.case["id"], d.case.get("category", "-"), d.case.get("severity", "major")))
            lines.append("        参考 %s：%s" % (reference_name, _describe_sequence(d.reference)))
            lines.append("        候选 %s：%s" % (name, _describe_sequence(d.candidate)))
    return lines, diffs_by_impl


def _describe_sequence(case_result):
    """描述一次用例执行的完整调用序列；单次调用保持简洁，重复调用逐次列出。"""
    outcomes = case_result.outcomes
    if len(outcomes) == 1:
        return outcomes[0].describe()
    return "；".join(
        "第 %d 次%s" % (index, outcome.describe())
        for index, outcome in enumerate(outcomes, start=1)
    )


def render_verdicts(diffs_by_impl, reference_name):
    """兼容性判定：可替换 / 有条件替换 / 不可替换。"""
    lines = ["【兼容性判定】（基准：%s）" % reference_name]
    verdicts = {}
    for name, diffs in diffs_by_impl.items():
        verdict, reason = judge(diffs)
        verdicts[name] = verdict
        lines.append("  [%s] %s" % (name, verdict))
        lines.append("      依据：%s" % reason)
        if diffs:
            points = ", ".join("%s(%s)" % (d.case["id"], d.case.get("severity", "major"))
                               for d in diffs)
            lines.append("      不一致点：%s" % points)
    return lines, verdicts


def full_report(cases, results_by_impl, reference_name):
    lines = []
    lines += render_coverage(cases, results_by_impl, reference_name)
    lines.append("")
    lines += render_failures(cases, results_by_impl)
    lines.append("")
    diff_lines, diffs_by_impl = render_diffs(cases, results_by_impl, reference_name)
    lines += diff_lines
    lines.append("")
    verdict_lines, _ = render_verdicts(diffs_by_impl, reference_name)
    lines += verdict_lines
    return "\n".join(lines)
