#!/usr/bin/env python3
"""框架自测：用注入的不稳定测试验证框架本身的判定质量。

检查项：
  1. 误判：稳定通过的测试不得被判为 flaky（报告误判数据）。
  2. 检出：注入的 flaky 测试（已知失败率）必须被检出；稳定失败必须判为 stable_fail。
  3. 顺序相关性：victim 测试在固定/打乱两种模式下失败率差异必须被识别。
  4. 隔离：隔离清单可增删查、可审计，隔离测试仍被执行并单独汇报。
退出码 0 表示全部通过。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from flakyhunter.core import JsonlSink, execute_round, load_records, load_tests
from flakyhunter.judge import judge_records
from flakyhunter.order_analysis import analyze_order
from flakyhunter.quarantine import QuarantineRegistry

HERE = os.path.dirname(os.path.abspath(__file__))
REPEATS = 50
failures = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}")
    if not ok:
        failures.append(name)


def run_suite(tests, repeats, modes=("fixed", "shuffled"), seed=12345, jobs=1):
    records = []
    for round_index in range(repeats):
        for mode in modes:
            records += execute_round(tests, run_id="selftest", round_index=round_index,
                                     mode=mode, seed=seed * 1000 + round_index, jobs=jobs)
    return records


print("== 1. 误判检查：稳定测试不得被判为 flaky ==")
all_tests = load_tests(os.path.join(HERE, "injected_tests.py"))
stable = {k: v for k, v in all_tests.items() if k.startswith("test_stable_")
          and k != "test_stable_fail"}
records = run_suite(stable, REPEATS)
verdicts = {v.test_id: v for v in judge_records(records)}
misjudged = [t for t, v in verdicts.items() if v.verdict != "stable_pass"]
total_runs = sum(v.runs for v in verdicts.values())
print(f"  稳定测试 {len(stable)} 个 x {REPEATS * 2} 次执行 = {total_runs} 条记录，"
      f"误判为 flaky 的测试数: {len(misjudged)} (误判率 "
      f"{len(misjudged)}/{len(stable)} = {len(misjudged)/len(stable):.1%})")
check("稳定测试零误判", not misjudged, f"misjudged={misjudged}")
min_conf = min(v.confidence for v in verdicts.values())
print(f"  stable_pass 判定最低置信度: {min_conf:.4f} (n={REPEATS * 2}, p0=0.05)")

print("== 2. 检出检查：注入 flaky 必须被检出，稳定失败必须判 stable_fail ==")
records = run_suite(all_tests, REPEATS)
verdicts = {v.test_id: v for v in judge_records(records)}
for tid, expect in [("test_flaky_p010", "flaky"), ("test_flaky_p030", "flaky"),
                    ("test_flaky_p050", "flaky"), ("test_flaky_p080", "flaky"),
                    ("test_stable_fail", "stable_fail")]:
    v = verdicts[tid]
    check(f"{tid} -> {expect}", v.verdict == expect,
          f"(实际={v.verdict}, 失败率={v.failure_rate:.3f} "
          f"CI=[{v.ci_low:.3f},{v.ci_high:.3f}])")

print("== 3. 顺序相关性：固定 vs 打乱对比 ==")
order_tests = load_tests(os.path.join(HERE, "order_tests.py"))
records = run_suite(order_tests, REPEATS)
effects = {e.test_id: e for e in analyze_order(records)}
victim = effects["test_order_b_victim"]
print(f"  victim: fixed {victim.fixed_failures}/{victim.fixed_runs} 失败, "
      f"shuffled {victim.shuffled_failures}/{victim.shuffled_runs} 失败")
check("victim 固定顺序下高失败率", victim.fixed_rate > 0.9,
      f"fixed_rate={victim.fixed_rate:.2f}")
check("victim 被标记为顺序相关", victim.order_dependent,
      f"shuffled_rate={victim.shuffled_rate:.2f}")
check("independent 不被标记为顺序相关",
      not effects["test_order_c_independent"].order_dependent)

print("== 4. 隔离清单：可审计、仍执行、到期复查 ==")
with tempfile.TemporaryDirectory() as tmp:
    qpath = os.path.join(tmp, "quarantine.json")
    registry = QuarantineRegistry(qpath)
    entry = registry.add("test_flaky_p050", by="selftest-bot",
                         reason="注入的不稳定测试，验证隔离流程",
                         review_after="2000-01-01")  # 已过期 -> 应出现在 due 中
    check("隔离记录包含审计字段",
          all(k in entry for k in
              ("quarantined_by", "reason", "quarantined_at", "review_after")))
    check("到期复查列表非空", len(registry.due()) == 1)
    # 隔离后仍执行并单独汇报
    sink = JsonlSink(os.path.join(tmp, "results.jsonl"))
    sink.write(run_suite(all_tests, 5))
    qids = QuarantineRegistry(qpath).ids()
    recs = load_records(sink.path)
    isolated_runs = [r for r in recs if r["test_id"] in qids]
    check("隔离测试仍被执行", len(isolated_runs) == 10,
          f"runs={len(isolated_runs)}")
    try:
        registry.add("test_x", by="", reason="", review_after="2000-01-01")
        check("缺少操作人/原因时拒绝隔离", False)
    except ValueError:
        check("缺少操作人/原因时拒绝隔离", True)

print()
if failures:
    print(f"自测失败 {len(failures)} 项: {failures}")
    sys.exit(1)
print("自测全部通过")
