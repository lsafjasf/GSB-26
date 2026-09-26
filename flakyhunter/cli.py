"""命令行入口：

  python -m flakyhunter run   --tests FILE --repeats N --mode both --out results.jsonl
  python -m flakyhunter judge --results results.jsonl [--p0 0.05]
  python -m flakyhunter order --results results.jsonl
  python -m flakyhunter quarantine add --id TEST --by WHO --reason WHY --review-after YYYY-MM-DD
  python -m flakyhunter quarantine list|due|remove --id TEST
  python -m flakyhunter report --results results.jsonl
"""
from __future__ import annotations

import argparse
import random
import sys
import uuid

from .core import JsonlSink, execute_round, load_records, load_tests
from .judge import DEFAULT_P0, judge_records
from .order_analysis import analyze_order
from .quarantine import QuarantineRegistry

DEFAULT_QUARANTINE = "quarantine.json"


def cmd_run(args) -> int:
    tests = load_tests(args.tests)
    quarantine = QuarantineRegistry(args.quarantine)
    sink = JsonlSink(args.out)
    run_id = uuid.uuid4().hex[:12]
    modes = ["fixed", "shuffled"] if args.mode == "both" else [args.mode]
    rng = random.Random(args.seed)
    total = 0
    for round_index in range(args.repeats):
        for mode in modes:
            seed = rng.randrange(2**31)
            records = execute_round(tests, run_id=run_id, round_index=round_index,
                                    mode=mode, seed=seed, jobs=args.jobs)
            # 隔离测试打标记，report 时单独汇报（但仍正常执行）
            qids = quarantine.ids()
            for rec in records:
                if rec.test_id in qids:
                    rec.mode = rec.mode  # 模式不变
            sink.write(records)
            total += len(records)
    print(f"run_id={run_id} 执行 {args.repeats} 轮 x {len(modes)} 模式 x "
          f"{len(tests)} 测试 = {total} 条记录 -> {args.out}")
    return 0


def cmd_judge(args) -> int:
    records = load_records(args.results)
    quarantine = QuarantineRegistry(args.quarantine)
    qids = quarantine.ids()
    verdicts = judge_records(records, p0=args.p0)
    flaky = 0
    for v in verdicts:
        tag = " [QUARANTINED]" if v.test_id in qids else ""
        print(v.explain() + tag)
        if v.verdict == "flaky":
            flaky += 1
    print(f"\n共 {len(verdicts)} 个测试：flaky={flaky}，"
          f"阈值 p0={args.p0}（stable 判定置信度 = 1-(1-p0)^n）")
    return 1 if flaky and not args.allow_flaky else 0


def cmd_order(args) -> int:
    records = load_records(args.results)
    effects = analyze_order(records)
    print(f"{'test_id':<40} {'fixed':>10} {'shuffled':>10}  顺序相关?")
    for e in effects:
        print(f"{e.test_id:<40} "
              f"{e.fixed_failures:>4}/{e.fixed_runs:<5} "
              f"{e.shuffled_failures:>4}/{e.shuffled_runs:<5}  "
              f"{'YES' if e.order_dependent else 'no'}")
    return 0


def cmd_quarantine(args) -> int:
    registry = QuarantineRegistry(args.quarantine)
    if args.action == "add":
        entry = registry.add(args.id, args.by, args.reason, args.review_after)
        print(f"已隔离 {entry['test_id']} (by={entry['quarantined_by']}, "
              f"review_after={entry['review_after']})")
    elif args.action == "remove":
        print("已移除" if registry.remove(args.id) else "未找到该测试")
    elif args.action == "list":
        for e in registry.entries:
            print(f"{e['test_id']}: by={e['quarantined_by']} "
                  f"at={e['quarantined_at']} review_after={e['review_after']}\n"
                  f"    reason: {e['reason']}")
    elif args.action == "due":
        due = registry.due()
        for e in due:
            print(f"到期需复查: {e['test_id']} (review_after={e['review_after']}, "
                  f"by={e['quarantined_by']})")
        if not due:
            print("没有到期需复查的隔离测试")
    return 0


def cmd_report(args) -> int:
    records = load_records(args.results)
    quarantine = QuarantineRegistry(args.quarantine)
    qids = quarantine.ids()
    verdicts = judge_records(records, p0=args.p0)
    normal = [v for v in verdicts if v.test_id not in qids]
    isolated = [v for v in verdicts if v.test_id in qids]
    print("== 普通测试集 ==")
    for v in normal:
        print("  " + v.explain().replace("\n", "\n  "))
    print("== 隔离测试集（仍定期执行并汇报）==")
    if not isolated:
        print("  （空）")
    for v in isolated:
        print("  " + v.explain().replace("\n", "\n  "))
    due = quarantine.due()
    if due:
        print("== 到期需复查 ==")
        for e in due:
            print(f"  {e['test_id']} (review_after={e['review_after']})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="flakyhunter",
                                     description="不稳定测试识别框架")
    parser.add_argument("--quarantine", default=DEFAULT_QUARANTINE,
                        help="隔离清单文件路径")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="重复执行测试集并记录结果")
    p.add_argument("--tests", required=True, help="包含 test_* 函数的 Python 文件")
    p.add_argument("--repeats", type=int, default=20, help="重复轮数")
    p.add_argument("--mode", choices=["fixed", "shuffled", "both"], default="both")
    p.add_argument("--jobs", type=int, default=1, help="并行度（记录进环境信息）")
    p.add_argument("--seed", type=int, default=20260927, help="主随机种子")
    p.add_argument("--out", required=True, help="JSONL 输出文件")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("judge", help="稳定性判定")
    p.add_argument("--results", required=True)
    p.add_argument("--p0", type=float, default=DEFAULT_P0, help="最低关心的失败率")
    p.add_argument("--allow-flaky", action="store_true",
                   help="发现 flaky 时退出码仍为 0")
    p.set_defaults(func=cmd_judge)

    p = sub.add_parser("order", help="顺序相关性对比（固定 vs 打乱）")
    p.add_argument("--results", required=True)
    p.set_defaults(func=cmd_order)

    p = sub.add_parser("quarantine", help="隔离清单管理")
    p.add_argument("action", choices=["add", "remove", "list", "due"])
    p.add_argument("--id", help="测试名")
    p.add_argument("--by", default="", help="操作人（可审计）")
    p.add_argument("--reason", default="", help="隔离原因（可审计）")
    p.add_argument("--review-after", default="", help="复查日期 YYYY-MM-DD")
    p.set_defaults(func=cmd_quarantine)

    p = sub.add_parser("report", help="分普通/隔离两集汇报")
    p.add_argument("--results", required=True)
    p.add_argument("--p0", type=float, default=DEFAULT_P0)
    p.set_defaults(func=cmd_report)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "quarantine" and args.action in ("add", "remove") and not args.id:
        print("quarantine add/remove 需要 --id", file=sys.stderr)
        return 2
    return args.func(args)
