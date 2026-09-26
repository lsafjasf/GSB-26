"""框架自测：用注入的已知行为测试集验证框架判定逻辑。

验证点：
  A. 分类正确性 —— 每类注入测试都被判成预期类别；
  B. 误判率实验 —— 多轮独立会话中，稳定测试被误判为「不稳定」的次数（应为 0）；
  C. 顺序相关性 —— test_order_victim 被标记为「顺序相关」；
  D. 隔离机制 —— 隔离后仍执行、单独汇报、审计字段完整、不影响退出码。

运行：python3 -m selftest.run_selftest   （在仓库根目录执行）
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flaky_framework.core import run_suite
from flaky_framework.judge import (classify, summarize_by_test, order_dependence,
                                   STABLE_PASS, STABLE_FAIL, FLAKY)
from flaky_framework.quarantine import add, load, active_ids
from flaky_framework.report import build_report

MODULE = "selftest.suite"

EXPECTATIONS = {
    "test_stable_addition": STABLE_PASS,
    "test_stable_string": STABLE_PASS,
    "test_stable_list": STABLE_PASS,
    "test_stable_dict": STABLE_PASS,
    "test_stable_tuple": STABLE_PASS,
    "test_stable_failure": STABLE_FAIL,
    "test_flaky_random": FLAKY,
    "test_order_victim": FLAKY,
    "test_order_source": STABLE_PASS,
}

REPEATS = 20          # 每种模式重复次数
FP_SESSIONS = 5       # 误判实验的独立会话数


def short(test_id):
    return test_id.rsplit(".", 1)[-1]


def check_a_classification():
    print("=" * 70)
    print("A. 分类正确性（repeats=%d, mode=both, jobs=2）" % REPEATS)
    print("=" * 70)
    results = run_suite(MODULE, repeats=REPEATS, mode="both", jobs=2, seed=42)
    agg = summarize_by_test(results["records"])
    ok = True
    for test_id in sorted(agg):
        v = classify(agg[test_id]["failures"], agg[test_id]["n"])
        expected = EXPECTATIONS[short(test_id)]
        mark = "OK " if v["label"] == expected else "FAIL"
        if v["label"] != expected:
            ok = False
        print(f"  [{mark}] {short(test_id):<24} 失败 "
              f"{agg[test_id]['failures']:>2}/{agg[test_id]['n']:<2} "
              f"-> {v['label']}（期望 {expected}，置信度 {v['confidence']:.1%}）")
    return ok, results


def check_b_false_positives():
    print()
    print("=" * 70)
    print(f"B. 稳定测试误判实验（{FP_SESSIONS} 个独立会话 x {REPEATS} 次重复）")
    print("=" * 70)
    stable_ids = [t for t, e in EXPECTATIONS.items() if e == STABLE_PASS]
    total = fp = 0
    for s in range(FP_SESSIONS):
        results = run_suite(MODULE, repeats=REPEATS, mode="both", jobs=2,
                            seed=1000 + s)
        agg = summarize_by_test(results["records"])
        for test_id in agg:
            if short(test_id) in stable_ids:
                total += 1
                v = classify(agg[test_id]["failures"], agg[test_id]["n"])
                if v["label"] == FLAKY:
                    fp += 1
    print(f"  稳定测试判定总次数: {total}，被误判为不稳定: {fp}，"
          f"误判率 {fp / total:.2%}")
    print("  （判定规则决定：只有 0<失败数<总数 才判不稳定，")
    print("    真正稳定的测试失败数恒为 0，理论上误判率必为 0）")
    return fp == 0


def check_c_order_dependence(results):
    print()
    print("=" * 70)
    print("C. 顺序相关性分析（固定顺序 vs 打乱顺序）")
    print("=" * 70)
    od = order_dependence(results["records"])
    ok = True
    for test_id in sorted(od):
        o = od[test_id]
        fr = f"{o['fixed']['failures']}/{o['fixed']['n']}" if o["fixed"]["n"] else "-"
        sr = f"{o['shuffled']['failures']}/{o['shuffled']['n']}" if o["shuffled"]["n"] else "-"
        print(f"  {short(test_id):<24} 固定 {fr:>6}  打乱 {sr:>6}  {o['flag'] or '-'}")
    victim = od[f"{MODULE}.test_order_victim"]
    if victim["flag"] != "顺序相关":
        print("  [FAIL] test_order_victim 未被标记为顺序相关")
        ok = False
    else:
        print("  [OK ] test_order_victim 被正确标记为顺序相关")
    return ok


def check_d_quarantine():
    print()
    print("=" * 70)
    print("D. 隔离机制（隔离后仍执行、单独汇报、可审计、不影响退出码）")
    print("=" * 70)
    with tempfile.TemporaryDirectory() as tmp:
        qfile = os.path.join(tmp, "quarantine.json")
        target = f"{MODULE}.test_flaky_random"
        add(qfile, target, who="selftest-bot",
            reason="随机失败约 30%，根因排查中", review_days=14)
        entry = load(qfile)["entries"][0]
        audit_ok = all(entry.get(k) for k in
                       ("who", "reason", "quarantined_at", "review_after"))
        print(f"  审计字段: who={entry['who']} reason={entry['reason']}")
        print(f"            quarantined_at={entry['quarantined_at']}")
        print(f"            review_after={entry['review_after']}")
        print(f"  [{'OK ' if audit_ok else 'FAIL'}] 审计字段完整")

        results = run_suite(MODULE, repeats=10, mode="fixed", jobs=2, seed=7)
        text, code = build_report(results, qfile)
        still_ran = any(r["test_id"] == target
                        for rec in results["records"] for r in rec["results"])
        in_report = "[隔离]" in text and target in text
        # 套件里还有非隔离的稳定失败测试，退出码应为 1；
        # 单独验证：把稳定失败也隔离后退出码应变 0。
        add(qfile, f"{MODULE}.test_stable_failure", who="selftest-bot",
            reason="演示用稳定失败", review_days=14)
        _, code_all_q = build_report(results, qfile)
        print(f"  [{'OK ' if still_ran else 'FAIL'}] 隔离测试仍被执行 "
              f"({sum(1 for rec in results['records'] for r in rec['results'] if r['test_id'] == target)} 次)")
        print(f"  [{'OK ' if in_report else 'FAIL'}] 隔离测试在报告中单独列出")
        print(f"  [{'OK ' if code == 1 else 'FAIL'}] 存在非隔离失败时退出码=1 (实际 {code})")
        print(f"  [{'OK ' if code_all_q == 0 else 'FAIL'}] 全部问题隔离后退出码=0 (实际 {code_all_q})")
        return audit_ok and still_ran and in_report and code == 1 and code_all_q == 0


def main():
    ok_a, results = check_a_classification()
    ok_b = check_b_false_positives()
    ok_c = check_c_order_dependence(results)
    ok_d = check_d_quarantine()
    print()
    print("=" * 70)
    print(f"自测总结: A分类={'通过' if ok_a else '失败'} "
          f"B误判={'通过' if ok_b else '失败'} "
          f"C顺序={'通过' if ok_c else '失败'} "
          f"D隔离={'通过' if ok_d else '失败'}")
    print("=" * 70)
    return 0 if all([ok_a, ok_b, ok_c, ok_d]) else 1


if __name__ == "__main__":
    sys.exit(main())
