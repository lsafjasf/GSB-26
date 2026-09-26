"""字段声明校验：缺字段 / 类型不符 / 未知字段 / 未知事件，均需报错并指出埋点位置。"""

import inspect
import unittest

from structured.event import InstrumentationError
from structured.logger import emit


def _call_line():
    return inspect.currentframe().f_back.f_lineno + 1


class ValidationTest(unittest.TestCase):
    def assert_location(self, ctx, line):
        self.assertIn(f"{__file__}:{line}", str(ctx.exception))

    def test_missing_required_field(self):
        with self.assertRaises(InstrumentationError) as ctx:
            line = _call_line()
            emit("order_created", order_id="O1", user_id="u1", total=11)
        self.assert_location(ctx, line)
        self.assertIn("缺少必填字段", str(ctx.exception))
        self.assertIn("phone", str(ctx.exception))

    def test_wrong_type(self):
        with self.assertRaises(InstrumentationError) as ctx:
            line = _call_line()
            emit("order_created", order_id="O1", user_id="u1", total="11", phone="138")
        self.assert_location(ctx, line)
        self.assertIn("类型不符", str(ctx.exception))
        self.assertIn("total", str(ctx.exception))

    def test_unknown_field(self):
        with self.assertRaises(InstrumentationError) as ctx:
            line = _call_line()
            emit("user_logged_in", user_id="u1", email="a@b.c", ip="1.1.1.1", foo=1)
        self.assert_location(ctx, line)
        self.assertIn("未声明字段", str(ctx.exception))

    def test_unknown_event(self):
        with self.assertRaises(InstrumentationError) as ctx:
            line = _call_line()
            emit("no_such_event", a=1)
        self.assert_location(ctx, line)
        self.assertIn("未声明的事件类型", str(ctx.exception))

    def test_error_param_must_be_exception(self):
        with self.assertRaises(InstrumentationError) as ctx:
            line = _call_line()
            emit("settlement_failed", order_id="O1", error="boom")
        self.assert_location(ctx, line)

    def test_optional_error_info(self):
        from structured.logger import collect
        with collect() as events:
            emit("settlement_failed", order_id="O1", error=TimeoutError("gateway timeout"))
        (event,) = events
        self.assertEqual(event.error.type, "TimeoutError")
        self.assertEqual(event.error.message, "gateway timeout")
        self.assertIn("TimeoutError", event.error.stack)


if __name__ == "__main__":
    unittest.main()
