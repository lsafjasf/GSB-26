"""边界用例集：月末、闰年 2 月、不存在的日期、节假日落在目标日、
空节假日表、单日规则、连续多日非工作日、跨年、三种顺延策略。"""
import unittest
from datetime import date, datetime, time

from calrule import (MonthlyDayOfMonth, MonthlyNthWeekday, MonthlyNthWorkday,
                     Schedule, Weekly, WorkCalendar)

T9 = time(9, 0)


def dt(y, m, d, hh=9, mm=0):
    return datetime(y, m, d, hh, mm)


class EdgeCaseTest(unittest.TestCase):
    def test_empty_holiday_table(self):
        cal = WorkCalendar()  # 空节假日表，默认周六日
        sch = Schedule([MonthlyDayOfMonth(15)], cal)
        # 2024-06-15 是周六，顺延到周一 17 日
        self.assertEqual(sch.next_after(dt(2024, 6, 1)), dt(2024, 6, 17))
        sch_none = Schedule([MonthlyDayOfMonth(15)], cal, roll='none')
        self.assertEqual(sch_none.next_after(dt(2024, 6, 1)), dt(2024, 6, 15))

    def test_single_day_weekly_rule(self):
        cal = WorkCalendar()
        sch = Schedule([Weekly({2})], cal)  # 仅每周三
        self.assertEqual(sch.next_after(dt(2024, 6, 3)), dt(2024, 6, 5))   # 周一 -> 周三
        self.assertEqual(sch.next_after(dt(2024, 6, 5, 9, 0)), dt(2024, 6, 12))  # 恰在触发点 -> 下周
        self.assertEqual(sch.next_after(dt(2024, 6, 5, 8, 59)), dt(2024, 6, 5, 9, 0))

    def test_month_end_31_skips_short_months(self):
        cal = WorkCalendar()
        sch = Schedule([MonthlyDayOfMonth(31)], cal, roll='none')
        # 2024-04 只有 30 天 -> 跳到 5 月 31 日
        self.assertEqual(sch.next_after(dt(2024, 4, 1)), dt(2024, 5, 31))
        self.assertEqual(sch.next_after(dt(2024, 1, 31, 9, 0)), dt(2024, 3, 31))  # 2 月无 31 日

    def test_leap_year_feb29(self):
        cal = WorkCalendar()
        sch = Schedule([MonthlyDayOfMonth(29)], cal, roll='none')
        # 2023 年 2 月无 29 日 -> 3 月 29 日
        self.assertEqual(sch.next_after(dt(2023, 2, 1)), dt(2023, 3, 29))
        # 闰年 2024-02-29 存在（周四，工作日）
        self.assertEqual(sch.next_after(dt(2024, 1, 30)), dt(2024, 2, 29))
        # 2024-02-29 之后 -> 2024-03-29
        self.assertEqual(sch.next_after(dt(2024, 2, 29, 9, 0)), dt(2024, 3, 29))

    def test_holiday_exactly_on_target(self):
        cal = WorkCalendar(holidays={date(2024, 5, 1), date(2024, 5, 2), date(2024, 5, 3)})
        sch = Schedule([MonthlyDayOfMonth(1)], cal)
        self.assertEqual(sch.next_after(dt(2024, 4, 15)), dt(2024, 5, 6))  # 5/1-5/3 节假日 + 周末 -> 5/6
        sch_b = Schedule([MonthlyDayOfMonth(1)], cal, roll='backward')
        self.assertEqual(sch_b.next_after(dt(2024, 4, 15)), dt(2024, 4, 30))  # 前挪到 4/30

    def test_consecutive_multi_day_nonwork(self):
        # 连续 9 天非工作日：周末 5/4-5/5 + 节假日 5/6-5/10 + 周末 5/11-5/12
        hols = {date(2024, 5, d) for d in range(6, 11)}
        cal = WorkCalendar(holidays=hols)
        # 区间跳转表与周末跳跃叠加：5/4 -> 5/13（周一）
        self.assertEqual(cal.next_workday(date(2024, 5, 4)), date(2024, 5, 13))
        self.assertEqual(cal.prev_workday(date(2024, 5, 12)), date(2024, 5, 3))
        sch = Schedule([MonthlyDayOfMonth(6)], cal)
        self.assertEqual(sch.next_after(dt(2024, 4, 20)), dt(2024, 5, 13))
        sch_b = Schedule([MonthlyDayOfMonth(6)], cal, roll='backward')
        self.assertEqual(sch_b.next_after(dt(2024, 4, 20)), dt(2024, 5, 3))

    def test_long_run_backward_roll(self):
        # 长非工作日区间跨触发点：backward 顺延需越过多个原始触发点
        hols = {date(2024, 1, d) for d in range(1, 32)}  # 整个 1 月
        cal = WorkCalendar(holidays=hols)
        sch = Schedule([MonthlyDayOfMonth(15)], cal, roll='backward')
        # 1/15 与 2/15 都前挪：1/15 -> 2023-12-29(五)，2/15 -> 2023-12-29? 不，2/15 前挪到 1 月区间前
        # 2024-02-15 前挪：2/15..2/? 非节假日，仅 1 月。2/15 是周四工作日 -> 自身
        self.assertEqual(sch.next_after(dt(2024, 1, 20)), dt(2024, 2, 15))

    def test_nth_workday_of_month(self):
        cal = WorkCalendar(holidays={date(2024, 6, 3)})  # 6/3 周一节假日
        sch = Schedule([MonthlyNthWorkday(1)], cal)
        self.assertEqual(sch.next_after(dt(2024, 5, 20)), dt(2024, 6, 4))  # 第 1 个工作日 6/4
        sch_last = Schedule([MonthlyNthWorkday(-1)], cal)
        self.assertEqual(sch_last.next_after(dt(2024, 6, 1)), dt(2024, 6, 28))  # 6/28 周五

    def test_nth_weekday_including_nonexistent_5th(self):
        cal = WorkCalendar()
        sch = Schedule([MonthlyNthWeekday(0, 5)], cal, roll='none')  # 第 5 个周一
        # 2024-06 只有 4 个周一 -> 7/29
        self.assertEqual(sch.next_after(dt(2024, 6, 1)), dt(2024, 7, 29))
        sch_last_fri = Schedule([MonthlyNthWeekday(4, -1)], cal, roll='none')
        self.assertEqual(sch_last_fri.next_after(dt(2024, 6, 1)), dt(2024, 6, 28))

    def test_cross_year(self):
        cal = WorkCalendar(holidays={date(2024, 12, 31), date(2025, 1, 1)})
        sch = Schedule([MonthlyDayOfMonth(31)], cal)
        # 12/31 是节假日 -> 顺延到 2025-01-02（1/1 也是节假日）
        self.assertEqual(sch.next_after(dt(2024, 12, 15)), dt(2025, 1, 2))
        sch_none = Schedule([MonthlyDayOfMonth(31)], cal, roll='none')
        self.assertEqual(sch_none.next_after(dt(2024, 12, 31, 9, 0)), dt(2025, 1, 31))

    def test_rule_union_takes_min(self):
        cal = WorkCalendar()
        sch = Schedule([Weekly({0}), MonthlyDayOfMonth(10)], cal, roll='none')
        # 2024-06-03 是周一(周规则 6/3 已过 9 点? dt 为 6/1)：周规则 6/3，月规则 6/10 -> 取 6/3
        self.assertEqual(sch.next_after(dt(2024, 6, 1)), dt(2024, 6, 3))

    def test_forward_roll_from_previous_occurrence(self):
        # dt 在「原始触发日之后、顺延结果之前」：应命中顺延结果
        cal = WorkCalendar(holidays={date(2024, 6, 10)})  # 6/10 周一节假日
        sch = Schedule([MonthlyDayOfMonth(10)], cal)
        # 6/10 顺延到 6/11；dt=6/10 12:00 在原始触发点之后，但顺延结果 6/11 仍 > dt
        self.assertEqual(sch.next_after(dt(2024, 6, 10, 12, 0)), dt(2024, 6, 11))
        # dt 超过顺延结果 -> 下一周期 7/10
        self.assertEqual(sch.next_after(dt(2024, 6, 11, 9, 0)), dt(2024, 7, 10))

    def test_custom_weekends(self):
        cal = WorkCalendar(weekends={4, 5})  # 周五周六休息
        sch = Schedule([Weekly({4})], cal)   # 每周五触发，逢周五休息顺延
        # 5/31(五) 顺延到 6/2(日)，晚于 dt=6/1，故命中 6/2
        self.assertEqual(sch.next_after(dt(2024, 6, 1)), dt(2024, 6, 2))
        # 6/7(五) 顺延到 6/9(日)
        self.assertEqual(sch.next_after(dt(2024, 6, 2, 9, 0)), dt(2024, 6, 9))

    def test_time_of_day_boundary(self):
        cal = WorkCalendar()
        sch = Schedule([MonthlyDayOfMonth(15)], cal, tod=time(8, 30), roll='none')
        self.assertEqual(sch.next_after(dt(2024, 6, 15, 8, 29)), datetime(2024, 6, 15, 8, 30))
        self.assertEqual(sch.next_after(dt(2024, 6, 15, 8, 30)), datetime(2024, 7, 15, 8, 30))


if __name__ == '__main__':
    unittest.main(verbosity=2)
