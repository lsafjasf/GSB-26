"""文本报告生成与退出码逻辑。"""
from __future__ import annotations

from . import judge
from .quarantine import active_ids, due_for_review


def build_report(results, quarantine_path=None, min_rate=judge.DEFAULT_MIN_RATE):
    records = results["records"]
    q_ids = active_ids(quarantine_path) if quarantine_path else set()
    due = due_for_review(quarantine_path) if quarantine_path else []

    agg = judge.summarize_by_test(records)
    order = judge.order_dependence(records)

    lines = []
    env = results["env"]
    lines.append("=" * 78)
    lines.append("不稳定测试识别报告")
    lines.append("=" * 78)
    lines.append(f"测试模块: {results['module']}   每模式重复: {results['repeats_per_mode']} 次"
                 f"   模式: {', '.join(results['modes'])}")
    lines.append(f"环境: seed={env['seed']} jobs={env['jobs']} "
                 f"python={env['python']} cpu={env['cpu_count']}")
    lines.append(f"判定参数: 最小可检测失败率 p0={min_rate:.0%}，"
                 f"达到 95% 置信度需重复 {judge.needed_repeats(min_rate)} 次")
    lines.append("")

    header = f"{'测试':<46} {'失败/总数':>9} {'判定':<6} {'置信度':>7}  顺序相关性"
    lines.append(header)
    lines.append("-" * 78)

    quarantined_rows = []
    has_active_problem = False
    for test_id in sorted(agg):
        a = agg[test_id]
        v = judge.classify(a["failures"], a["n"], min_rate)
        od = order.get(test_id, {})
        flag = od.get("flag", "") or "-"
        row = (f"{test_id:<46} {a['failures']:>4}/{a['n']:<4} "
               f"{v['label']:<6} {v['confidence']:>6.1%}  {flag}")
        if test_id in q_ids:
            quarantined_rows.append((row, v, od))
            continue
        lines.append(row)
        if v["label"] in (judge.STABLE_FAIL, judge.FLAKY):
            has_active_problem = True

    if quarantined_rows:
        lines.append("")
        lines.append(f"隔离集合（仍照常执行，单独汇报，不计入退出码）: {len(quarantined_rows)} 个")
        lines.append("-" * 78)
        for row, v, od in quarantined_rows:
            lines.append("[隔离] " + row)
            lines.append(f"       现状: {v['reason']}")
            if od.get("fixed_rate") is not None:
                lines.append(f"       顺序对比: 固定 {od['fixed']['failures']}/{od['fixed']['n']}"
                             f" vs 打乱 {od['shuffled']['failures']}/{od['shuffled']['n']}")

    if due:
        lines.append("")
        lines.append("到期需复查的隔离项:")
        for e in due:
            lines.append(f"  - {e['test_id']} (隔离人 {e['who']}, 复查截止 {e['review_after']})")

    lines.append("")
    lines.append("判定依据示例:")
    for test_id in sorted(agg)[:3]:
        a = agg[test_id]
        v = judge.classify(a["failures"], a["n"], min_rate)
        lines.append(f"  {test_id}: {v['reason']}")

    return "\n".join(lines), (1 if has_active_problem else 0)
