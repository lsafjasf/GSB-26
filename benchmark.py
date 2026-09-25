"""性能与跳转次数基准：
1. 连续计算 100,000 次 next_after 的耗时；
2. 跨年 / 长顺延 / 稀疏规则场景下的跳转次数（证明不逐日推进）；
3. 同场景下逐日暴力实现的耗时对照。
"""
import time
from datetime import date, datetime, timedelta

from brute_force import brute_next_after
from calrule import (MonthlyDayOfMonth, MonthlyNthWeekday, MonthlyNthWorkday,
                     Schedule, Weekly, WorkCalendar)


def build_calendar():
    """2019-2031 年节假日：每月 1 个 1~4 天的簇（含跨周末的长区间）。"""
    holidays = set()
    for year in range(2019, 2032):
        for month in range(1, 13):
            start = date(year, month, (year * 7 + month * 11) % 24 + 1)
            for k in range((year + month) % 4 + 1):
                holidays.add(start + timedelta(days=k))
    return WorkCalendar(holidays=holidays)


def bench_throughput(sch, n=100_000):
    dt = datetime(2024, 1, 1)
    t0 = time.perf_counter()
    for _ in range(n):
        dt = sch.next_after(dt)
    elapsed = time.perf_counter() - t0
    print(f"[吞吐] {n:,} 次 next_after（链式，约推进到 {dt.date()}）: "
          f"{elapsed:.3f}s, 平均 {elapsed / n * 1e6:.2f} us/次")
    return elapsed


def bench_jumps():
    cal = build_calendar()
    # 专用日历：2024-01-12 ~ 2024-01-19 节假日，与前后周末连成 12~21 日共 10 天非工作日
    long_run_cal = WorkCalendar(
        holidays={date(2024, 1, d) for d in range(12, 20)})
    cases = [
        ("跨年: 每月31日, 2024-12-15 -> 2024-12-31",
         Schedule([MonthlyDayOfMonth(31)], cal), datetime(2024, 12, 15)),
        ("稀疏: 每月29日, 2023-02-01 -> 2023-03-29 (平年2月无29日)",
         Schedule([MonthlyDayOfMonth(29)], cal, roll='none'), datetime(2023, 2, 1)),
        ("闰年: 每月29日, 2024-01-30 -> 2024-02-29",
         Schedule([MonthlyDayOfMonth(29)], cal, roll='none'), datetime(2024, 1, 30)),
        ("稀疏: 第5个周一, 2024-06-01 -> 2024-07-29 (6月仅4个周一)",
         Schedule([MonthlyNthWeekday(0, 5)], cal, roll='none'), datetime(2024, 6, 1)),
        ("长顺延: 每月12日, 目标日落入10天非工作日区间 -> 2024-01-22",
         Schedule([MonthlyDayOfMonth(12)], long_run_cal), datetime(2024, 1, 1)),
        ("组合: 每周三 + 每月15日 + 每月第1个工作日",
         Schedule([Weekly({2}), MonthlyDayOfMonth(15), MonthlyNthWorkday(1)], cal),
         datetime(2024, 12, 28)),
    ]
    print("[跳转次数] (逐日暴力需数百次日期判断)")
    for name, sch, dt in cases:
        stats = {}
        nxt = sch.next_after(dt, stats)
        days = (nxt.date() - dt.date()).days
        total = sum(stats.values())
        print(f"  {name}: 跨 {days:>3} 天, 跳转 {total:>2} 次, 明细={stats}")


def bench_brute_compare(sch, dt, n=200):
    t0 = time.perf_counter()
    for _ in range(n):
        brute_next_after(sch, dt)
    elapsed = time.perf_counter() - t0
    print(f"[对照] 逐日暴力 {n} 次单次查询: {elapsed:.3f}s, "
          f"平均 {elapsed / n * 1e3:.2f} ms/次")


def main():
    cal = build_calendar()
    sch = Schedule(
        [Weekly({2}), MonthlyDayOfMonth(15), MonthlyNthWorkday(1),
         MonthlyNthWeekday(4, -1)],
        cal, roll='forward')
    bench_throughput(sch)
    print()
    bench_jumps()
    print()
    bench_brute_compare(sch, datetime(2024, 6, 1))


if __name__ == '__main__':
    main()
