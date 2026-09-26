"""内存基准：连续处理 100 万请求，测量滑动窗口实现的内存占用。

理论关系：存储条目数 <= R * window_seconds（R 为请求速率，条/秒），
实现额外允许 <= 2 倍摊还冗余 + 1024 条常数，与请求总量无关。

用法：python3 memory_benchmark.py [请求数]
"""

import sys
import tracemalloc

from replay_protector import ReplayProtector


def main():
    total = int(sys.argv[1]) if len(sys.argv) > 1 else 1_000_000
    window = 60.0
    skew = 5.0
    rate = 1000.0  # 条/秒
    now = 1_000_000.0

    p = ReplayProtector(window, skew, rejection_log_size=10_000)
    tracemalloc.start()
    t0 = now
    for i in range(total):
        now = t0 + i / rate
        # 10% 乱序（窗口内倒退），1% 重放，贴近真实流量
        ts = now
        rid = f"req-{i}"
        if i % 10 == 0 and i > 0:
            ts = now - (i % 50) * 1.0
        if i % 100 == 0 and i > 0:
            rid = f"req-{i - 1}"
        p.check(rid, ts, now=now)
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    ideal_bound = int(rate * window)
    print(f"请求总量:        {total:,}")
    print(f"窗口/漂移/速率:  {window}s / {skew}s / {rate:.0f} 条每秒")
    print(f"留存条目数:      {p.stored_ids:,}")
    print(f"理论理想上界:    {ideal_bound:,} 条 (R * window)")
    print(f"实现上界:        {2 * ideal_bound + 1024:,} 条 (2x 摊还 + 1024)")
    print(f"tracemalloc 当前: {current / 1024:.1f} KiB")
    print(f"tracemalloc 峰值: {peak / 1024:.1f} KiB")
    print(f"拒绝日志条数:    {len(p.rejection_log):,} (有界 deque)")
    assert p.stored_ids <= 2 * ideal_bound + 1024, "超出实现内存上界!"
    print("OK: 内存有界，与请求总量无关")


if __name__ == "__main__":
    main()
