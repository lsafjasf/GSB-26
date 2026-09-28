"""边界情形与核心逻辑单元测试（标准库 unittest）。"""
from __future__ import annotations

import random
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dataset import gen_card, gen_id, fmt_grouped          # noqa: E402
from sensid import Scanner, normalization_diff              # noqa: E402
from sensid.detectors import _luhn_ok                       # noqa: E402
from sensid.normalize import normalize_text                 # noqa: E402

ZW = "​"      # 零宽空格
ZWJ = "‍"     # 零宽连接符
WJ = "⁠"      # Word Joiner
BOM = "﻿"     # 零宽不换行空格


def _weave(num: str, ch: str = ZW) -> str:
    """在号码每位数字之间插入不可见字符。"""
    return ch.join(num)


def _strip_cf(s: str) -> str:
    return normalize_text(s)[0]


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


class TestZeroWidthNormalization(unittest.TestCase):
    def setUp(self) -> None:
        self.scanner = Scanner()

    def test_zero_width_inside_phone_detected(self):
        text = f"联系电话 {_weave('13800138000')} 请回拨"
        r = self.scanner.scan(text)
        hits = [m for m in r.matches if m.type == "phone"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].normalized, "13800138000")

    def test_zero_width_inside_id_and_card_detected(self):
        rng = random.Random(11)
        idnum, card = gen_id(rng), gen_card(rng)
        text = f"身份证 {_weave(idnum, ZWJ)}，卡号 {_weave(card, WJ)}"
        r = self.scanner.scan(text)
        self.assertTrue(any(m.type == "id_card" and m.normalized == idnum
                            for m in r.matches))
        self.assertTrue(any(m.type == "bank_card" and m.normalized == card
                            for m in r.matches))

    def test_offsets_map_back_to_original(self):
        phone = _weave("13800138000", BOM)
        text = f"前缀 {phone} 后缀"
        m = next(x for x in self.scanner.scan(text).matches if x.type == "phone")
        # 报告位置指向原文：切片还原后等于命中内容（含零宽字符）
        self.assertEqual(text[m.start:m.end], m.raw)
        self.assertEqual(_strip_cf(m.raw), "13800138000")
        self.assertEqual(m.start, text.index(phone))
        self.assertEqual(m.end, text.index(phone) + len(phone))

    def test_normalization_diff_lists_recovered(self):
        phone = _weave("13800138000")
        text = f"手机 {phone}，订单号 20260929103015"
        diff = normalization_diff(text)
        self.assertIn(("phone", text.index(phone), text.index(phone) + len(phone),
                       "13800138000"), diff["recovered"])
        self.assertEqual(diff["lost"], [])

    def test_no_new_false_positives_on_benign_text(self):
        benign = (
            f"订单号：2026{ZW}0929{ZW}103015，共 12 件。\n"
            f"时间戳 2026{ZW}0929{ZW}103015 已记录。\n"
            f"批次编号 {ZWJ}2026{ZWJ}0929{ZWJ}，数量 100。\n"
            f"版本 v1{ZW}.{ZW}2{ZW}.{ZW}3 发布。\n"
        )
        with_norm = self.scanner.scan(benign)
        without_norm = Scanner(normalize=False).scan(benign)
        self.assertEqual(with_norm.matches, [])
        self.assertLessEqual(len(with_norm.matches), len(without_norm.matches))


if __name__ == "__main__":
    unittest.main()
