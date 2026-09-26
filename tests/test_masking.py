"""脱敏：规则集中在 masking.py，输出时统一脱敏，事件内保留原始值。"""

import unittest

from structured import masking
from structured.event import Field
from structured.logger import collect, emit


class MaskingTest(unittest.TestCase):
    def test_rules_centralized(self):
        self.assertEqual(masking.apply("phone", "13812345678"), "138****5678")
        self.assertEqual(masking.apply("email", "alice@example.com"), "a***@example.com")
        self.assertEqual(masking.apply("card", "6222020200112233"), "************2233")

    def test_render_masks_but_event_keeps_raw(self):
        with collect() as events:
            emit("order_created", order_id="O1", user_id="u1", total=11,
                 phone="13812345678")
        (event,) = events
        # 事件内保留原始值：信息不丢失
        self.assertEqual(event.fields["phone"], "13812345678")
        # 渲染输出时脱敏
        self.assertEqual(event.render()["fields"]["phone"], "138****5678")

    def test_unknown_mask_rule_rejected_at_declaration(self):
        with self.assertRaises(ValueError):
            Field("secret", str, mask="no_such_rule")


if __name__ == "__main__":
    unittest.main()
