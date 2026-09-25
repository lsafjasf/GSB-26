"""对拍：随机生成日历 + 规则 + 起始时间，calrule（跳转）vs brute_force（逐日）。

每个用例从同一 dt 出发连续取 5 次 next_after，两侧结果必须完全一致。
用法: python3 differential_test.py [用例数] [随机种子]
"""
import random
import sys
from datetime import date, datetime, time, timedelta

from brute_force import brute_next_after
from calrule import (MonthlyDayOfMonth, MonthlyNthWeekday, MonthlyNthWorkday,
                     Schedule, Weekly, WorkCalendar)


def random_calendar(rng, base_year):
    weekends = rng.choice([(5, 6), (6, 0), (5,), (6,), (0, 6)])
    holidays = set()
    for year in range(base_year - 1, base_year + 4):
        # 空节假日表的情形
        if rng.random() < 0.1:
            continue
        for _ in range(rng.randint(0, 10)):
            start = date(year, rng.randint(1, 12), rng.randint(1, 28))
            # 节假日簇：长度 1~6，容易与周末连成多日非工作日
            for k in range(rng.randint(1, 6)):
                holidays.add(start + timedelta(days=k))
    return WorkCalendar(weekends=weekends, holidays=holidays)


def random_rule(rng):
    kind = rng.randrange(4)
    if kind == 0:
        return MonthlyDayOfMonth(rng.randint(1, 31))  # 含 29/30/31 等可能不存在的日期
    if kind == 1:
        return MonthlyNthWorkday(rng.choice([1, 2, 3, 5, 10, 20, -1, -2, -3]))
    if kind == 2:
        return MonthlyNthWeekday(rng.randrange(7), rng.choice([1, 2, 3, 4, 5, -1, -2]))
    return Weekly(rng.sample(range(7), rng.randint(1, 3)))


def random_schedule(rng, cal):
    rules = [random_rule(rng) for _ in range(rng.randint(1, 3))]
    tod = time(rng.randrange(24), rng.choice([0, 15, 30, 45]))
    roll = rng.choice(['forward', 'backward', 'none'])
    return Schedule(rules, cal, tod=tod, roll=roll)


def run_case(rng, case_id):
    base_year = rng.randint(2023, 2026)
    cal = random_calendar(rng, base_year)
    sch = random_schedule(rng, cal)
    dt = datetime(base_year, rng.randint(1, 12), rng.randint(1, 28),
                  rng.randrange(24), rng.choice([0, 1, 29, 30, 31, 59]))
    for step in range(5):  # 链式取 5 次，覆盖跨年
        fast = sch.next_after(dt)
        slow = brute_next_after(sch, dt)
        if fast != slow:
            print(f"[MISMATCH] case={case_id} step={step}")
            print(f"  dt={dt} roll={sch.roll} tod={sch.tod}")
            print(f"  weekends={sorted(cal.weekends)} holidays={sorted(cal.holidays)}")
            print(f"  rules={sch.rules!r}")
            print(f"  fast={fast} slow={slow}")
            return False
        dt = fast
    return True


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1500
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 20260926
    rng = random.Random(seed)
    bad = 0
    for i in range(n):
        if not run_case(rng, i):
            bad += 1
            if bad >= 5:
                break
    if bad:
        print(f"FAILED: {bad} 个用例不一致")
        sys.exit(1)
    print(f"OK: {n} 个用例 x 5 步链式对拍全部一致 (seed={seed})")


if __name__ == '__main__':
    main()
