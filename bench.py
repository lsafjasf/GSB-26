"""吞吐与边界情形基准：空文本、超大文本、含换行/分隔符号码、异常字符。"""
from __future__ import annotations

import os
import random
import time

from dataset import build_dataset, gen_card
from sensid import Scanner

SIZES_MB = [1, 8, 32]


def _build_big_text(target_bytes: int) -> str:
    base, _ = build_dataset()
    base_bytes = len(base.encode("utf-8"))
    parts, total = [], 0
    while total < target_bytes:
        parts.append(base)
        total += base_bytes
    return "".join(parts)


def main() -> None:
    os.makedirs("results", exist_ok=True)
    scanner = Scanner()
    lines = ["# 吞吐与边界情形基准", ""]

    lines.append("## 边界情形")
    lines.append("")
    lines.append("| 情形 | 结果 | 耗时 |")
    lines.append("|---|---|---|")

    r = scanner.scan("")
    lines.append(f"| 空文本 | 命中 {len(r.matches)}，无异常 | {r.elapsed_ms:.2f} ms |")

    r = scanner.scan("   \n\t\r\n  ")
    lines.append(f"| 纯空白文本 | 命中 {len(r.matches)}，无异常 | {r.elapsed_ms:.2f} ms |")

    weird = "😀​１２３\ud800\udfff 零宽​字符 １３８００１３８０００ ★"
    r = scanner.scan(weird)
    lines.append(f"| 异常字符（emoji/零宽/孤立代理项/全角数字） | 命中 {len(r.matches)}，无异常 | {r.elapsed_ms:.2f} ms |")

    raw_card = gen_card(random.Random(1))
    card = " ".join(raw_card[i:i + 4] for i in range(0, len(raw_card), 4))
    card = card.replace(" ", "\n", 1)  # 强制包含一个换行符
    r = scanner.scan(f"卡号 {card} 请查收")
    ok = any(m.type == "bank_card" for m in r.matches)
    lines.append(f"| 号码内含换行/空格 | 银行卡命中={ok} | {r.elapsed_ms:.2f} ms |")

    r = scanner.scan("电话 138-0013-8000 或 139 1234 5678")
    n_phone = sum(1 for m in r.matches if m.type == "phone")
    lines.append(f"| 含连字符/空格的手机号 | 手机号命中 {n_phone}/2 | {r.elapsed_ms:.2f} ms |")

    lines.append("")
    lines.append("## 吞吐（含真实命中与否定判定）")
    lines.append("")
    lines.append("| 文本大小 | 耗时 | 吞吐 | 命中数 | 否定候选数 |")
    lines.append("|---|---|---|---|---|")
    for mb in SIZES_MB:
        text = _build_big_text(mb * 1024 * 1024)
        nbytes = len(text.encode("utf-8"))
        t0 = time.perf_counter()
        r = scanner.scan(text)
        dt = time.perf_counter() - t0
        speed = nbytes / dt / 1024 / 1024
        lines.append(
            f"| {nbytes / 1024 / 1024:.1f} MB | {dt:.2f} s | {speed:.1f} MB/s "
            f"| {len(r.matches)} | {len(r.rejected)} |"
        )

    report = "\n".join(lines) + "\n"
    with open("results/benchmark.md", "w", encoding="utf-8") as f:
        f.write(report)
    print(report)


if __name__ == "__main__":
    main()
