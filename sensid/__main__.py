"""命令行入口：python -m sensid scan [文件路径]（缺省读标准输入）。"""
from __future__ import annotations

import argparse
import json
import sys

from .engine import Scanner, normalization_diff


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sensid", description="敏感数据发现")
    parser.add_argument("command", choices=["scan"])
    parser.add_argument("path", nargs="?", help="待扫描文本文件，缺省读 stdin")
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--show-rejected", action="store_true", help="同时输出被否定的候选")
    parser.add_argument("--diff-normalization", action="store_true",
                        help="逐条列出规范化（剔除零宽/格式控制字符）前后的命中集合差异")
    args = parser.parse_args(argv)

    if args.path:
        with open(args.path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    else:
        text = sys.stdin.read()

    result = Scanner(threshold=args.threshold).scan(text)
    out = {
        "matches": [
            {"type": m.type, "start": m.start, "end": m.end, "raw": m.raw,
             "score": round(m.score, 3), "reasons": m.reasons}
            for m in result.matches
        ],
        "conflicts": [
            {"kept": f"{c.kept_type}@{c.kept_span}",
             "dropped": f"{c.dropped_type}@{c.dropped_span}", "rule": c.rule}
            for c in result.conflicts
        ],
        "stats": {"text_len": result.text_len, "elapsed_ms": round(result.elapsed_ms, 2)},
    }
    if args.show_rejected:
        out["rejected"] = [
            {"type": r.type, "start": r.start, "end": r.end, "raw": r.raw, "reason": r.reason}
            for r in result.rejected
        ]
    if args.diff_normalization:
        diff = normalization_diff(text, threshold=args.threshold)
        out["normalization_diff"] = {
            "recovered": [
                {"type": t, "start": s, "end": e, "normalized": n}
                for t, s, e, n in diff["recovered"]
            ],
            "lost": [
                {"type": t, "start": s, "end": e, "normalized": n}
                for t, s, e, n in diff["lost"]
            ],
        }
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
