"""误差界验证：Space-Saving 近似结果与精确计数（collections.Counter）对拍。

覆盖两类极端分布：
  1. 重尾分布（Zipf，少数键占据绝大多数流量）
  2. 海量不同键（绝大部分键只出现一次）

对每种容量 m，验证：对所有键（含未出现在摘要中的键），
    |est(x) - exact(x)| <= N / m
并统计 Top-K 精度。运行：python3 verify_error.py
"""

import random
import sys
from collections import Counter

from space_saving import SpaceSaving

N = 500_000          # 每种分布的流长度
CAPACITIES = [100, 1_000, 10_000]
TOPK = 100
SEED = 42


def gen_zipf(n, vocab=10_000, seed=SEED):
    """重尾：权重 1/i 的 Zipf，头部键占绝大部分流量。"""
    rng = random.Random(seed)
    keys = list(range(vocab))
    weights = [1.0 / (i + 1) for i in range(vocab)]
    return rng.choices(keys, weights=weights, k=n)


def gen_many_distinct(n, hot=50, hot_ratio=0.05, seed=SEED):
    """海量不同键：5% 流量来自 50 个热键，其余 95% 每个键只出现一次。"""
    rng = random.Random(seed)
    stream = []
    n_hot = int(n * hot_ratio)
    for _ in range(n_hot):
        stream.append(rng.randrange(hot))
    for i in range(n - n_hot):
        stream.append(hot + i)  # 每个键唯一
    rng.shuffle(stream)
    return stream


def verify(name, stream):
    exact = Counter(stream)
    n = len(stream)
    print(f"\n=== 分布: {name} | N={n:,} | 不同键数={len(exact):,} ===")
    header = f"{'容量 m':>8} | {'误差界 N/m':>12} | {'实际最大误差':>12} | {'界/实际 余量':>12} | {'Top-%d 精度' % TOPK:>10} | {'误差<=界':>8}"
    print(header)
    print("-" * len(header))
    exact_top = {k for k, _ in exact.most_common(TOPK)}
    all_ok = True
    for m in CAPACITIES:
        ss = SpaceSaving(m)
        for x in stream:
            ss.update(x)
        bound = ss.error_bound()
        # 全量对拍：摘要中的键 + 精确计数中所有键（覆盖未监控键）
        max_err = 0
        for key, est in ss.counts.items():
            err = abs(est - exact[key])
            if err > max_err:
                max_err = err
        for key, cnt in exact.items():
            if key not in ss.counts:
                if cnt > max_err:
                    max_err = cnt
        ok = max_err <= bound + 1e-9
        all_ok &= ok
        # Top-K 精度
        approx_top = {k for k, _ in ss.topk(TOPK)}
        precision = len(approx_top & exact_top) / TOPK
        # 监控保证：所有真实计数 > 误差界的键必须被监控
        guaranteed = all(key in ss.counts for key, c in exact.items() if c > bound)
        assert guaranteed, "监控保证被破坏！"
        print(f"{m:>8} | {bound:>12.1f} | {max_err:>12} | {bound / max(max_err, 1):>11.2f}x | {precision:>10.2%} | {'PASS' if ok else 'FAIL':>8}")
    return all_ok


def main():
    ok = True
    ok &= verify("重尾 Zipf (vocab=10k)", gen_zipf(N))
    ok &= verify("海量不同键 (95% 只出现一次)", gen_many_distinct(N))
    print("\n总体结果:", "全部通过 —— 实际误差均未超过报告界 N/m" if ok else "存在失败！")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
