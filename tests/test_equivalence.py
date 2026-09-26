"""等价性对拍：同一业务路径分别跑重构前（字符串日志）与重构后（结构化事件），
断言旧日志中出现的每一个关键值都能在新事件的对应字段中找到（信息零丢失）。
"""

import contextlib
import io
import re
import unittest

from legacy.order_service import OrderService as LegacyOrderService
from structured.logger import collect
from structured.order_service import OrderService as NewOrderService

ITEMS = [("apple", 3, 2), ("pear", 5, 1)]  # total = 11


def scenario(svc):
    """同一条业务路径，对重构前/后的服务各跑一遍。"""
    svc.login("u1001", "alice@example.com", "10.0.0.8")
    oid = svc.create_order("u1001", ITEMS, "13812345678")
    svc.pay(oid, 11, "6222020200112233")
    try:
        svc.pay(oid, 10, "6222020200112233")
    except Exception:
        pass
    svc.refund(oid, 11, "user request")
    svc.settle(oid)


# 旧字符串日志 -> 新事件类型 的解析器；group 名即新事件字段名（对照表见 docs/mapping.md）
LEGACY_PATTERNS = [
    ("user_logged_in",
     re.compile(r"LOGIN uid=(?P<user_id>\S+) email=(?P<email>\S+) ip=(?P<ip>\S+)")),
    ("order_created",
     re.compile(r"\[ORDER\] create ok uid=(?P<user_id>\S+) oid=(?P<order_id>\S+) "
                r"total=(?P<total>\S+) phone=(?P<phone>\S+)")),
    ("payment_succeeded",
     re.compile(r"pay success \| order:(?P<order_id>\S+) \| amount:(?P<amount>\S+) "
                r"\| card:(?P<card_no>\S+)")),
    ("payment_failed",
     re.compile(r"PAY FAIL! order (?P<order_id>\S+) expect=(?P<expected>\S+) "
                r"got=(?P<got>\S+)")),
    ("order_refunded",
     re.compile(r"refund>> oid=(?P<order_id>\S+) amount=(?P<amount>\S+) "
                r"reason=(?P<reason>.*)")),
    ("settlement_failed",
     re.compile(r"ERROR settle order=(?P<order_id>\S+) failed: "
                r"(?P<error_type>\w+): (?P<error_message>.*)")),
]


def run_legacy():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        scenario(LegacyOrderService())
    records = []
    for line in buf.getvalue().splitlines():
        for name, pattern in LEGACY_PATTERNS:
            m = pattern.search(line)
            if m:
                records.append((name, m.groupdict()))
                break
        else:
            raise AssertionError(f"无法解析的旧日志行: {line!r}")
    return records


def run_new():
    with collect() as events:
        scenario(NewOrderService())
    return events


class EquivalenceTest(unittest.TestCase):
    def test_no_information_loss(self):
        legacy_records = run_legacy()
        events = run_new()

        # 1) 埋点数量与事件序列一一对应
        self.assertEqual([n for n, _ in legacy_records],
                         [e.name for e in events])

        # 2) 旧日志中的每个关键值，都能在新事件对应字段中找到
        for (name, old_values), event in zip(legacy_records, events):
            for key, old_value in old_values.items():
                if key == "error_type":
                    new_value = event.error.type
                elif key == "error_message":
                    new_value = event.error.message
                else:
                    self.assertIn(key, event.fields,
                                  f"事件 {name} 缺少与旧日志对应的字段 {key}")
                    new_value = str(event.fields[key])
                self.assertEqual(old_value, new_value,
                                 f"事件 {name} 字段 {key} 与旧日志不一致")

        # 3) 敏感字段：事件内保留原始值（等价性成立的前提），渲染输出已脱敏
        rendered = [e.render() for e in events]
        by_name = {e.name: r for e, r in zip(events, rendered)}
        self.assertEqual(by_name["order_created"]["fields"]["phone"], "138****5678")
        self.assertEqual(by_name["user_logged_in"]["fields"]["email"],
                         "a***@example.com")
        self.assertEqual(by_name["payment_succeeded"]["fields"]["card_no"],
                         "************2233")


if __name__ == "__main__":
    unittest.main()
