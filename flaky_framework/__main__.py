"""命令行入口。

用法示例：
  python -m flaky_framework run --module selftest.suite --repeats 20 \
      --mode both --jobs 4 --out results.json --quarantine quarantine.json
  python -m flaky_framework report results.json --quarantine quarantine.json
  python -m flaky_framework quarantine add --file quarantine.json \
      --test selftest.suite.test_flaky_random --who alice \
      --reason "随机失败，待排查" --review-days 14
  python -m flaky_framework quarantine list --file quarantine.json
  python -m flaky_framework quarantine resolve --file quarantine.json \
      --test selftest.suite.test_flaky_random --who bob --note "已修复"
"""
from __future__ import annotations

import argparse
import json
import sys

from .core import run_suite
from .judge import DEFAULT_MIN_RATE
from .quarantine import add, resolve, load, due_for_review
from .report import build_report


def main(argv=None):
    parser = argparse.ArgumentParser(prog="flaky_framework",
                                     description="不稳定测试识别框架")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="重复执行测试集并输出判定报告")
    p_run.add_argument("--module", required=True, help="测试模块（含 test_* 函数）")
    p_run.add_argument("--repeats", type=int, default=20, help="每种模式的重复次数")
    p_run.add_argument("--mode", choices=["fixed", "shuffled", "both"], default="both")
    p_run.add_argument("--jobs", type=int, default=1, help="并行执行的进程数")
    p_run.add_argument("--seed", type=int, default=42, help="随机种子（控制打乱顺序）")
    p_run.add_argument("--min-rate", type=float, default=DEFAULT_MIN_RATE,
                       help="最小可检测失败率 p0（判定阈值）")
    p_run.add_argument("--quarantine", default=None, help="隔离清单 JSON 路径")
    p_run.add_argument("--out", default=None, help="原始结果 JSON 输出路径")

    p_rep = sub.add_parser("report", help="根据已保存的结果 JSON 重新生成报告")
    p_rep.add_argument("results", help="run 子命令保存的结果 JSON")
    p_rep.add_argument("--quarantine", default=None)
    p_rep.add_argument("--min-rate", type=float, default=DEFAULT_MIN_RATE)

    p_q = sub.add_parser("quarantine", help="隔离清单管理")
    qsub = p_q.add_subparsers(dest="qcmd", required=True)
    for name in ("add", "list", "resolve"):
        qp = qsub.add_parser(name)
        qp.add_argument("--file", required=True)
        if name in ("add", "resolve"):
            qp.add_argument("--test", required=True)
            qp.add_argument("--who", required=True)
        if name == "add":
            qp.add_argument("--reason", required=True)
            qp.add_argument("--review-days", type=int, default=14)
        if name == "resolve":
            qp.add_argument("--note", default="")

    args = parser.parse_args(argv)

    if args.cmd == "run":
        results = run_suite(args.module, repeats=args.repeats, mode=args.mode,
                            jobs=args.jobs, seed=args.seed)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
        text, code = build_report(results, args.quarantine, args.min_rate)
        print(text)
        return code

    if args.cmd == "report":
        with open(args.results, encoding="utf-8") as f:
            results = json.load(f)
        text, code = build_report(results, args.quarantine, args.min_rate)
        print(text)
        return code

    if args.cmd == "quarantine":
        if args.qcmd == "add":
            add(args.file, args.test, args.who, args.reason, args.review_days)
            print(f"已隔离 {args.test}")
        elif args.qcmd == "resolve":
            resolve(args.file, args.test, args.who, args.note)
            print(f"已解除隔离 {args.test}")
        else:
            data = load(args.file)
            if not data["entries"]:
                print("（隔离清单为空）")
            for e in data["entries"]:
                print(f"[{e['status']}] {e['test_id']}\n"
                      f"    隔离人: {e['who']}  原因: {e['reason']}\n"
                      f"    隔离时间: {e['quarantined_at']}  复查截止: {e['review_after']}")
            due = due_for_review(args.file)
            if due:
                print(f"!! {len(due)} 项已到期需复查")
        return 0


if __name__ == "__main__":
    sys.exit(main())
