"""calrule: 日历规则计算库（仅标准库）。

按「工作日历 + 规则组合 + 顺延策略」计算下一次执行时间。
所有计算按规则逐级跳转（按月 / 按周 / 按非工作日区间），不逐日暴力推进。

核心概念：
- WorkCalendar   : 工作日定义（周末 + 节假日表），构造时把连续非工作日
                   合并成「区间跳转表」，顺延为均摊 O(1)。
- 规则（Rule）    : MonthlyDayOfMonth / MonthlyNthWorkday /
                   MonthlyNthWeekday / Weekly，规则之间取并集。
- Schedule       : 规则集合 + 工作日历 + 触发时刻 + 顺延策略
                   （forward 顺延 / backward 前挪 / none 不顺延）。

顺延语义（与 brute_force.py 的集合语义严格一致）：
设规则原始触发日期集合为 O，顺延映射为 roll(.)，则调度集合为
S = { roll(o) : o in O }（重复日期去重），next_after(dt) = min{ s in S : s > dt }。
"""
from __future__ import annotations

import calendar as _cal
from datetime import date, datetime, time, timedelta

ONE_DAY = timedelta(days=1)
_MAX_MONTH_HOPS = 12000   # 按月规则跳转上限（1000 年），防死循环
_MAX_ROLL_LOOPS = 100000  # backward 顺延下跨触发点循环上限


def _tick(stats, key, n=1):
    if stats is not None:
        stats[key] = stats.get(key, 0) + n


def _add_months(y, m, k):
    m0 = y * 12 + (m - 1) + k
    return m0 // 12, m0 % 12 + 1


class WorkCalendar:
    """工作日历：周末定义 + 节假日表，内置非工作日区间跳转表。"""

    def __init__(self, weekends=(5, 6), holidays=()):
        weekends = frozenset(weekends)
        if not weekends or not weekends <= set(range(7)) or len(weekends) == 7:
            raise ValueError("weekends 必须是 0..6 的非空真子集")
        self.weekends = weekends
        self.holidays = frozenset(holidays)
        self._build_skip_maps()

    def is_workday(self, d: date) -> bool:
        return d.weekday() not in self.weekends and d not in self.holidays

    def _is_nonworkday(self, d: date) -> bool:
        return d.weekday() in self.weekends or d in self.holidays

    def _build_skip_maps(self):
        """把每段「连续非工作日」合并为区间，区间内每天 -> 区间外最近一天。

        fwd[d] = 区间之后第一天（工作日）；bwd[d] = 区间之前第一天（工作日）。
        建表代价 O(节假日覆盖的总天数)，之后每次顺延为 O(区间数) 跳转。
        """
        fwd, bwd = {}, {}
        for h in sorted(self.holidays):
            if h in fwd:
                continue  # 所在区间已处理
            start = h
            while self._is_nonworkday(start - ONE_DAY):
                start -= ONE_DAY
            end = h
            while self._is_nonworkday(end + ONE_DAY):
                end += ONE_DAY
            after, before = end + ONE_DAY, start - ONE_DAY
            d = start
            while d <= end:
                fwd[d] = after
                bwd[d] = before
                d += ONE_DAY
        self._fwd = fwd
        self._bwd = bwd

    def _weekend_jump(self, weekday, direction):
        """从 weekday 出发，沿 direction 到最近非周末日所需的步数（1..7）。"""
        k = 1
        while (weekday + direction * k) % 7 in self.weekends:
            k += 1
        return k

    def next_workday(self, d: date, stats=None) -> date:
        """>= d 的第一个工作日（顺延）。按区间/周末块跳转，不逐日。"""
        while True:
            t = self._fwd.get(d)
            if t is not None:
                d = t
                _tick(stats, 'range_jumps')
                continue
            if d.weekday() in self.weekends:
                d += timedelta(days=self._weekend_jump(d.weekday(), +1))
                _tick(stats, 'weekend_jumps')
                continue
            return d

    def prev_workday(self, d: date, stats=None) -> date:
        """<= d 的第一个工作日（前挪）。"""
        while True:
            t = self._bwd.get(d)
            if t is not None:
                d = t
                _tick(stats, 'range_jumps')
                continue
            if d.weekday() in self.weekends:
                d -= timedelta(days=self._weekend_jump(d.weekday(), -1))
                _tick(stats, 'weekend_jumps')
                continue
            return d

    def nth_workday_of_month(self, y, m, n, stats=None):
        """某月第 n 个工作日（n>=1）或倒数第 |n| 个（n<=-1）。不存在返回 None。

        扫描时遇非工作日区间/周末块直接跳过，只逐个计数工作日，
        代价 O(该月工作日数) <= 31，视为按月粒度 O(1)。
        """
        last_day = _cal.monthrange(y, m)[1]
        if n >= 1:
            d, end, count = date(y, m, 1), date(y, m, last_day), 0
            while d <= end:
                t = self._fwd.get(d)
                if t is not None:
                    if t > end:
                        return None
                    d = t
                    _tick(stats, 'range_jumps')
                    continue
                if d.weekday() in self.weekends:
                    d += timedelta(days=self._weekend_jump(d.weekday(), +1))
                    _tick(stats, 'weekend_jumps')
                    continue
                count += 1
                if count == n:
                    return d
                d += ONE_DAY
            return None
        d, start, count = date(y, m, last_day), date(y, m, 1), 0
        while d >= start:
            t = self._bwd.get(d)
            if t is not None:
                if t < start:
                    return None
                d = t
                _tick(stats, 'range_jumps')
                continue
            if d.weekday() in self.weekends:
                d -= timedelta(days=self._weekend_jump(d.weekday(), -1))
                _tick(stats, 'weekend_jumps')
                continue
            count += 1
            if count == -n:
                return d
            d -= ONE_DAY
        return None


# ---------------------------------------------------------------- 规则

class MonthlyDayOfMonth:
    """每月第 day 天；该月不存在此日（如 2 月 30 日）则跳过该月。"""

    def __init__(self, day):
        if not 1 <= day <= 31:
            raise ValueError("day 必须在 1..31")
        self.day = day

    def occurrence_in_month(self, y, m, cal, stats=None):
        return date(y, m, self.day) if self.day <= _cal.monthrange(y, m)[1] else None


class MonthlyNthWorkday:
    """每月第 n 个工作日（n>=1）或倒数第 |n| 个（n<=-1）；不够则跳过该月。"""

    def __init__(self, n):
        if n == 0 or abs(n) > 31:
            raise ValueError("n 必须满足 1 <= |n| <= 31")
        self.n = n

    def occurrence_in_month(self, y, m, cal, stats=None):
        return cal.nth_workday_of_month(y, m, self.n, stats)


class MonthlyNthWeekday:
    """每月第 n 个星期 weekday（n>=1）或倒数第 |n| 个（n<=-1，如最后一个周五）。"""

    def __init__(self, weekday, n):
        if not 0 <= weekday <= 6:
            raise ValueError("weekday 必须在 0..6")
        if n == 0 or abs(n) > 5:
            raise ValueError("n 必须满足 1 <= |n| <= 5")
        self.weekday = weekday
        self.n = n

    def occurrence_in_month(self, y, m, cal, stats=None):
        if self.n >= 1:
            first = date(y, m, 1)
            offset = (self.weekday - first.weekday()) % 7
            d = first + timedelta(days=offset + 7 * (self.n - 1))
        else:
            last = date(y, m, _cal.monthrange(y, m)[1])
            offset = (last.weekday() - self.weekday) % 7
            d = last - timedelta(days=offset + 7 * (-self.n - 1))
        return d if d.month == m else None


class Weekly:
    """每周固定星期几（可多天）触发。"""

    def __init__(self, weekdays):
        weekdays = frozenset(weekdays)
        if not weekdays or not weekdays <= set(range(7)):
            raise ValueError("weekdays 必须是 0..6 的非空子集")
        self.weekdays = weekdays


def _monthly_first_after(rule, dt, tod, cal, stats):
    y, m = dt.year, dt.month
    for _ in range(_MAX_MONTH_HOPS):
        occ = rule.occurrence_in_month(y, m, cal, stats)
        if occ is not None and datetime.combine(occ, tod) > dt:
            return datetime.combine(occ, tod)
        y, m = _add_months(y, m, 1)
        _tick(stats, 'month_hops')
    raise ValueError("规则在给定日历下永不触发")


def _monthly_last_at_or_before(rule, dt, tod, cal, stats):
    y, m = dt.year, dt.month
    for _ in range(_MAX_MONTH_HOPS):
        occ = rule.occurrence_in_month(y, m, cal, stats)
        if occ is not None and datetime.combine(occ, tod) <= dt:
            return datetime.combine(occ, tod)
        y, m = _add_months(y, m, -1)
        _tick(stats, 'month_hops')
    return None


def _weekly_first_after(rule, dt, tod):
    base = dt.date()
    for k in range(8):  # 常数上界
        d = base + timedelta(days=k)
        if d.weekday() in rule.weekdays and datetime.combine(d, tod) > dt:
            return datetime.combine(d, tod)
    raise AssertionError("unreachable")


def _weekly_last_at_or_before(rule, dt, tod):
    base = dt.date()
    for k in range(8):
        d = base - timedelta(days=k)
        if d.weekday() in rule.weekdays and datetime.combine(d, tod) <= dt:
            return datetime.combine(d, tod)
    return None


def _first_occurrence_after(rule, dt, tod, cal, stats):
    if isinstance(rule, Weekly):
        return _weekly_first_after(rule, dt, tod)
    return _monthly_first_after(rule, dt, tod, cal, stats)


def _last_occurrence_at_or_before(rule, dt, tod, cal, stats):
    if isinstance(rule, Weekly):
        return _weekly_last_at_or_before(rule, dt, tod)
    return _monthly_last_at_or_before(rule, dt, tod, cal, stats)


# ---------------------------------------------------------------- 调度

class Schedule:
    """规则并集 + 工作日历 + 触发时刻 + 顺延策略。

    roll: 'forward' 顺延到下一个工作日 / 'backward' 前挪到上一个工作日 / 'none' 不顺延。
    """

    def __init__(self, rules, calendar, tod=time(9, 0), roll='forward'):
        rules = list(rules)
        if not rules:
            raise ValueError("至少需要一个规则")
        if roll not in ('forward', 'backward', 'none'):
            raise ValueError("roll 必须是 forward/backward/none")
        self.rules = rules
        self.calendar = calendar
        self.tod = tod
        self.roll = roll

    def _roll_date(self, d, stats):
        if self.roll == 'forward':
            return self.calendar.next_workday(d, stats)
        if self.roll == 'backward':
            return self.calendar.prev_workday(d, stats)
        return d

    def _next_for_rule(self, rule, dt, stats):
        cal, tod = self.calendar, self.tod
        if self.roll == 'none':
            return _first_occurrence_after(rule, dt, tod, cal, stats)
        candidates = []
        if self.roll == 'forward':
            # 顺延单调不减：只有「前一个原始触发点」可能顺延到 dt 之后，
            # 更早的触发点顺延结果不更晚（否则与区间连续性矛盾），只需检查它。
            o_prev = _last_occurrence_at_or_before(rule, dt, tod, cal, stats)
            if o_prev is not None:
                c = datetime.combine(self._roll_date(o_prev.date(), stats), tod)
                if c > dt:
                    candidates.append(c)
            o = _first_occurrence_after(rule, dt, tod, cal, stats)
            candidates.append(datetime.combine(self._roll_date(o.date(), stats), tod))
        else:  # backward：前挪单调不减，o_prev 前挪后仍 <= dt，只需向后找
            o = _first_occurrence_after(rule, dt, tod, cal, stats)
            for _ in range(_MAX_ROLL_LOOPS):
                c = datetime.combine(self._roll_date(o.date(), stats), tod)
                if c > dt:
                    candidates.append(c)
                    break
                o = _first_occurrence_after(rule, o, tod, cal, stats)
                _tick(stats, 'roll_loops')
            else:
                raise ValueError("backward 顺延下找不到有效触发点")
        return min(candidates)

    def next_after(self, dt: datetime, stats=None) -> datetime:
        """严格晚于 dt 的最近一次触发时间。stats 为 dict 时记录跳转次数。"""
        best = None
        for rule in self.rules:
            c = self._next_for_rule(rule, dt, stats)
            if best is None or c < best:
                best = c
        return best
