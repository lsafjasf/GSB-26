"""检测器：正则初筛 + 校验位/长度/前缀校验的组合判定。

每类数据的判定依据（置信度由各项得分累加，上限 1.0）：

id_card（居民身份证号）
  18 位：正则初筛(0.40) + 长度/格式(0.10) + ISO7064-MOD11-2 校验位(0.30) + 省级地区码(0.10)
         另要求出生日期为合法日期，否则直接否定。
  15 位（老版）：正则初筛(0.40) + 格式(0.10) + 出生日期合法(0.10)，无校验位，置信度天然偏低。

bank_card（银行卡号）
  正则初筛(0.40) + 长度 13-19 位(0.10) + Luhn 校验(0.30) + 已知 BIN 前缀(0.10)
  （62 银联 / 4 Visa / 51-55 Mastercard / 34,37 Amex / 35 JCB / 30,36,38 Diners）

phone（中国大陆手机号）
  正则初筛(0.40) + 11 位 1[3-9] 格式(0.10) + 号段前缀在运营商已分配号段表内(0.30)

passport（中国护照号）
  正则初筛(0.30) + E/G/P/S/D 前缀 + 8 位数字格式(0.15)
  护照号无公开校验位，基础分 0.45 低于默认阈值，必须依赖上下文关键词加分才会命中，
  以此压制 "E12345678" 这类产品编号的误报。
"""
from __future__ import annotations

import re
from datetime import date

from .types import Candidate, Verdict

# 号码内部允许的分隔符：空格、制表符、换行、连字符
_SEP = r"[\s\-]?"
_SEP_CHARS = " \t\r\n-‐‑"

# 省级地区码（GB/T 2260 前两位）
_PROVINCES = {
    "11", "12", "13", "14", "15", "21", "22", "23",
    "31", "32", "33", "34", "35", "36", "37",
    "41", "42", "43", "44", "45", "46", "50",
    "51", "52", "53", "54", "61", "62", "63", "64", "65",
    "71", "81", "82",
}

# 身份证校验位权重与映射（ISO 7064:1983 MOD 11-2）
_ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_ID_CHECK_CHARS = "10X98765432"

# 运营商已分配手机号段（前三位）
_PHONE_PREFIXES = {
    # 移动
    "134", "135", "136", "137", "138", "139", "147", "148",
    "150", "151", "152", "157", "158", "159", "172", "178",
    "182", "183", "184", "187", "188", "195", "197", "198",
    # 联通
    "130", "131", "132", "145", "146", "155", "156", "166",
    "171", "175", "176", "185", "186", "196",
    # 电信
    "133", "149", "153", "173", "177", "180", "181", "189",
    "190", "191", "193", "199",
    # 广电 / 虚拟运营商 / 物联网
    "192", "165", "167", "170", "162",
}

_ID18_RE = re.compile(r"\d{17}[\dXx]")
_ID15_RE = re.compile(r"\d{15}")
_CARD_RE = re.compile(r"(?:\d" + _SEP + r"){12,18}\d")
_PHONE_RE = re.compile(r"1[3-9](?:" + _SEP + r"\d){9}")
_PASSPORT_RE = re.compile(r"(?<![A-Za-z0-9])[EGPSDegpsd]\d{8}(?!\d)")


def _strip_sep(raw: str) -> str:
    return "".join(ch for ch in raw if ch not in _SEP_CHARS)


def _bounded(text: str, start: int, end: int) -> bool:
    """边界检查：候选前后不能是数字或 ASCII 字母（避免把 18 位身份证的 17 位数字
    前缀误当卡号），也不能是"分隔符+数字"（即属于更长数字串的一部分）。"""
    def is_word_char(ch: str) -> bool:
        return ch.isdigit() or (ch.isascii() and ch.isalpha())

    if start > 0:
        prev = text[start - 1]
        if is_word_char(prev):
            return False
        if prev in _SEP_CHARS and start > 1 and text[start - 2].isdigit():
            return False
    if end < len(text):
        nxt = text[end]
        if is_word_char(nxt):
            return False
        if nxt in _SEP_CHARS and end + 1 < len(text) and text[end + 1].isdigit():
            return False
    return True


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _id_check_char(body17: str) -> str:
    s = sum(int(a) * w for a, w in zip(body17, _ID_WEIGHTS))
    return _ID_CHECK_CHARS[s % 11]


def _valid_date(year: int, month: int, day: int) -> bool:
    try:
        date(year, month, day)
    except ValueError:
        return False
    return True


def _card_bin(digits: str) -> str | None:
    if digits.startswith("62"):
        return "银联(62)"
    if digits.startswith("4"):
        return "Visa(4)"
    if digits[:2] in {"51", "52", "53", "54", "55"}:
        return "Mastercard(51-55)"
    if digits[:2] in {"34", "37"}:
        return "Amex(34/37)"
    if digits.startswith("35"):
        return "JCB(35)"
    if digits[:2] in {"30", "36", "38"}:
        return "Diners(30/36/38)"
    return None


class Detector:
    type: str = ""

    def find(self, text: str):
        raise NotImplementedError

    def validate(self, cand: Candidate) -> Verdict:
        raise NotImplementedError


class IdCardDetector(Detector):
    type = "id_card"

    def find(self, text: str):
        for m in _ID18_RE.finditer(text):
            if _bounded(text, m.start(), m.end()):
                yield Candidate(self.type, m.start(), m.end(), m.group(), m.group().upper())
        for m in _ID15_RE.finditer(text):
            if _bounded(text, m.start(), m.end()):
                yield Candidate(self.type, m.start(), m.end(), m.group(), m.group())

    def validate(self, cand: Candidate) -> Verdict:
        num = cand.normalized
        if len(num) == 18:
            if num[:2] not in _PROVINCES:
                return Verdict(False, 0.0, reject_reason=f"地区码 {num[:2]} 不在省级地区码表内")
            year, month, day = int(num[6:10]), int(num[10:12]), int(num[12:14])
            if not (1900 <= year <= 2100 and _valid_date(year, month, day)):
                return Verdict(False, 0.0, reject_reason=f"出生日期 {num[6:14]} 不是合法日期")
            expect = _id_check_char(num[:17])
            if num[17] != expect:
                return Verdict(False, 0.0,
                               reject_reason=f"校验位不匹配：期望 {expect}，实际 {num[17]}")
            return Verdict(True, 0.90, reasons=[
                "正则初筛通过(+0.40)", "18位长度/格式合法(+0.10)",
                f"ISO7064-MOD11-2 校验位通过(+0.30)", f"地区码 {num[:2]} 有效(+0.10)",
                f"出生日期 {num[6:14]} 合法",
            ])
        # 15 位老版身份证：无校验位
        year, month, day = 1900 + int(num[6:8]), int(num[8:10]), int(num[10:12])
        if not _valid_date(year, month, day):
            return Verdict(False, 0.0, reject_reason=f"出生日期 19{num[6:12]} 不是合法日期")
        return Verdict(True, 0.60, reasons=[
            "正则初筛通过(+0.40)", "15位老版格式(+0.10)",
            f"出生日期 19{num[6:12]} 合法(+0.10)", "老版15位无校验位，置信度上限受限",
        ])


class BankCardDetector(Detector):
    type = "bank_card"

    def find(self, text: str):
        for m in _CARD_RE.finditer(text):
            if _bounded(text, m.start(), m.end()):
                yield Candidate(self.type, m.start(), m.end(), m.group(), _strip_sep(m.group()))

    def validate(self, cand: Candidate) -> Verdict:
        num = cand.normalized
        if not (13 <= len(num) <= 19):
            return Verdict(False, 0.0, reject_reason=f"长度 {len(num)} 不在 13-19 位区间")
        if not _luhn_ok(num):
            return Verdict(False, 0.0, reject_reason="Luhn 校验失败")
        bin_name = _card_bin(num)
        score = 0.40 + 0.10 + 0.30
        reasons = ["正则初筛通过(+0.40)", f"长度 {len(num)} 位合法(+0.10)", "Luhn 校验通过(+0.30)"]
        if bin_name:
            score += 0.10
            reasons.append(f"BIN 前缀命中 {bin_name}(+0.10)")
        else:
            reasons.append("BIN 前缀未知，未加分")
        return Verdict(True, score, reasons=reasons)


class PhoneDetector(Detector):
    type = "phone"

    def find(self, text: str):
        for m in _PHONE_RE.finditer(text):
            if _bounded(text, m.start(), m.end()):
                yield Candidate(self.type, m.start(), m.end(), m.group(), _strip_sep(m.group()))

    def validate(self, cand: Candidate) -> Verdict:
        num = cand.normalized
        if len(num) != 11:
            return Verdict(False, 0.0, reject_reason=f"归一化后长度 {len(num)} != 11")
        prefix = num[:3]
        if prefix not in _PHONE_PREFIXES:
            return Verdict(False, 0.0, reject_reason=f"号段 {prefix} 不在运营商已分配号段表内")
        return Verdict(True, 0.80, reasons=[
            "正则初筛通过(+0.40)", "11位 1[3-9] 格式合法(+0.10)",
            f"号段 {prefix} 为已分配号段(+0.30)",
        ])


class PassportDetector(Detector):
    type = "passport"

    def find(self, text: str):
        for m in _PASSPORT_RE.finditer(text):
            if _bounded(text, m.start(), m.end()):
                yield Candidate(self.type, m.start(), m.end(), m.group(), m.group().upper())

    def validate(self, cand: Candidate) -> Verdict:
        num = cand.normalized
        if num[0] not in "EGPSD" or not num[1:].isdigit() or len(num) != 9:
            return Verdict(False, 0.0, reject_reason="护照号格式不合法")
        return Verdict(True, 0.45, reasons=[
            "正则初筛通过(+0.30)", f"前缀 {num[0]} + 8位数字格式合法(+0.15)",
            "护照号无公开校验位，需上下文关键词佐证",
        ])
