"""边界情形与核心逻辑单元测试（标准库 unittest）。"""
from __future__ import annotations

import random
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dataset import gen_card, gen_id, fmt_grouped          # noqa: E402
from sensid import Scanner                                  # noqa: E402
from sensid.detectors import _luhn_ok                       # noqa: E402


def _id_that_is_also_luhn_valid() -> str:
    """构造一个既是合法身份证（62 甘肃开头）又通过 Luhn 的 18 位串，用于冲突测试。"""
    rng = random.Random(7)
    while True:
        num = gen_id(rng)
        if num.startswith("62") and num[-1].isdigit() and _luhn_ok(num):
            return num


class TestEdgeCases(unittest.TestCase):
    def setUp(self) -> None:
        self.scanner = Scanner()

    def test_empty_text(self):
        r = self.scanner.scan("")
        self.assertEqual(r.matches, [])
        self.assertEqual(r.rejected, [])

    def test_whitespace_only(self):
        r = self.scanner.scan("  \n\t\r\n ")
        self.assertEqual(r.matches, [])

    def test_large_text(self):
        text = ("普通段落。" * 1000 + "\n") * 500  # 约 2.5MB
        t0 = time.perf_counter()
        r = self.scanner.scan(text)
        self.assertEqual(r.matches, [])
        self.assertLess(time.perf_counter() - t0, 30)

    def test_number_with_newline_and_separators(self):
        rng = random.Random(1)
        card = fmt_grouped(gen_card(rng), rng, allow_newline=True)
        r = self.scanner.scan(f"卡号 {card} 请查收")
        self.assertTrue(any(m.type == "bank_card" for m in r.matches))
        phone = "138-0013-8000"
        r = self.scanner.scan(f"电话 {phone}")
        self.assertTrue(any(m.type == "phone" and m.normalized == "13800138000"
                            for m in r.matches))

    def test_weird_unicode_no_crash(self):
        text = "😀​１２３\ud800\udfff 零宽​字符 ★ １３８００１３８０００"
        r = self.scanner.scan(text)  # 不应抛异常
        self.assertIsInstance(r.matches, list)

    def test_bad_checksum_id_rejected_with_reason(self):
        rng = random.Random(2)
        num = gen_id(rng)
        bad = num[:-1] + ("0" if num[-1] != "0" else "1")
        r = self.scanner.scan(f"号码 {bad}")
        self.assertFalse(any(m.type == "id_card" for m in r.matches))
        reasons = [x.reason for x in r.rejected if x.type == "id_card"]
        self.assertTrue(any("校验位" in x for x in reasons))

    def test_invalid_phone_prefix_rejected(self):
        r = self.scanner.scan("联系电话 14012345678")
        self.assertFalse(any(m.type == "phone" for m in r.matches))
        self.assertTrue(any("号段" in x.reason for x in r.rejected))

    def test_conflict_resolution_recorded(self):
        num = _id_that_is_also_luhn_valid()
        r = self.scanner.scan(f"证件 {num}")
        id_matches = [m for m in r.matches if m.type == "id_card"]
        self.assertEqual(len(id_matches), 1)
        overlapping = [m for m in r.matches if m.type == "bank_card"]
        self.assertEqual(overlapping, [])
        self.assertTrue(any(c.dropped_type == "bank_card" for c in r.conflicts))

    def test_passport_needs_context(self):
        bare = self.scanner.scan("编号 E12345678 一批")
        self.assertFalse(any(m.type == "passport" for m in bare.matches))
        with_ctx = self.scanner.scan("护照号码是 E12345678。")
        self.assertTrue(any(m.type == "passport" for m in with_ctx.matches))

    def test_determinism(self):
        text = "身份证 11010119900307789X，手机 13800138000"
        r1 = self.scanner.scan(text)
        r2 = Scanner().scan(text)
        self.assertEqual(
            [(m.type, m.start, m.end, m.score) for m in r1.matches],
            [(m.type, m.start, m.end, m.score) for m in r2.matches],
        )

    def test_threshold_filtering_explained(self):
        r = Scanner(threshold=0.95).scan("手机 13800138000")
        self.assertEqual(r.matches, [])
        self.assertTrue(any("低于阈值" in x.reason for x in r.rejected))


if __name__ == "__main__":
    unittest.main()
