"""brute_force: 逐日暴力参考实现，用于与 calrule 对拍。

刻意独立于 calrule 实现：逐日扫描、逐日顺延，逻辑尽量直白，
以保证对拍的有效性（两边同时错成一样的概率极低）。
"""
from __future__ import annotations

import calendar as _cal
from datetime import date, datetime, timedelta

from calrule import (MonthlyDayOfMonth, MonthlyNthWeekday, MonthlyNthWorkday,
                     Weekly, ONE_DAY)


def _is_workday(cal, d):
    return d.weekday() not in cal.weekends and d not in cal.holidays


def _is_raw_occurrence(rule, cal, d):
    """d 是否为规则的原始触发日（不考虑顺延）。"""
    if isinstance(rule, Weekly):
        return d.weekday() in rule.weekdays
    if isinstance(rule, MonthlyDayOfMonth):
        return d.day == rule.day
    if isinstance(rule, MonthlyNthWeekday):
        if d.weekday() != rule.weekday:
            return False
        if rule.n >= 1:
            return (d.day - 1) // 7 + 1 == rule.n
        return (_cal.monthrange(d.year, d.month)[1] - d.day) // 7 + 1 == -rule.n
    if isinstance(rule, MonthlyNthWorkday):
        if not _is_workday(cal, d):
            return False
        if rule.n >= 1:
            cnt = 0
            for day in range(1, d.day + 1):
                if _is_workday(cal, date(d.year, d.month, day)):
                    cnt += 1
            return cnt == rule.n
        cnt = 0
        last = _cal.monthrange(d.year, d.month)[1]
        for day in range(d.day, last + 1):
            if _is_workday(cal, date(d.year, d.month, day)):
                cnt += 1
        return cnt == -rule.n
    raise TypeError(rule)


def _roll(cal, d, policy):
    """逐日顺延/前挪。"""
    if policy == 'none':
        return d
    step = ONE_DAY if policy == 'forward' else -ONE_DAY
    while not _is_workday(cal, d):
        d += step
    return d


def brute_next_after(schedule, dt, lookback_days=120, horizon_days=1500):
    """调度集合 S = { roll(o) }（去重），返回 min{ s in S : s > dt }。

    lookback 覆盖「dt 之前的触发点被顺延到 dt 之后」的情形；
    horizon 覆盖稀疏规则（如 2 月 29 日）与长顺延区间。
    """
    cal, tod, policy = schedule.calendar, schedule.tod, schedule.roll
    best = None
    d = dt.date() - timedelta(days=lookback_days)
    end = dt.date() + timedelta(days=horizon_days)
    while d <= end:
        if any(_is_raw_occurrence(r, cal, d) for r in schedule.rules):
            cdt = datetime.combine(_roll(cal, d, policy), tod)
            if cdt > dt and (best is None or cdt < best):
                best = cdt
        d += ONE_DAY
    if best is None:
        raise ValueError("horizon 内无触发点")
    return best
