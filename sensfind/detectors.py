"""敏感数据检测器：正则初筛 + 多环节复核。

每个检测器分两阶段：
1. 正则初筛（宽松，保证召回）；
2. 校验复核：校验位 / 长度 / 前缀 / 上下文，任一硬校验不通过的候选
   记入 rejected 并说明否定原因；通过的候选给出置信度与判定理由。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

CONTEXT_WINDOW = 16  # 命中点前后各取多少字符做上下文分析

POSITIVE_CONTEXT = {
    "id_card": ["身份证", "证件号", "公民身份", "身份号码"],
    "bank_card": ["银行卡", "卡号", "信用卡", "借记卡", "储蓄卡", "账号", "账户", "收款"],
    "mobile": ["手机", "电话", "联系方式", "联系电话", "手机号"],
}
NEGATIVE_CONTEXT = ["订单", "单号", "编号", "快递", "物流", "发票", "条码", "流水号", "批次", "工单"]


@dataclass
class Candidate:
    type: str
    start: int          # 归一化文本中的偏移
    end: int
    raw: str            # 归一化文本中的原始片段
    value: str          # 归一化后的值（仅数字/校验位）
    confidence: float
    reasons: list = field(default_factory=list)


@dataclass
class Rejection:
    type: str
    start: int
    end: int
    raw: str
    stage: str          # 否定发生在哪个环节
    reason: str


def _context_score(text, start, end, data_type):
    """根据命中点附近的正/负向关键词调整置信度，返回 (增量, 理由列表)。"""
    lo = max(0, start - CONTEXT_WINDOW)
    hi = min(len(text), end + CONTEXT_WINDOW)
    window = text[lo:start] + text[end:hi]
    delta = 0.0
    reasons = []
    for kw in POSITIVE_CONTEXT[data_type]:
        if kw in window:
            delta += 0.10
            reasons.append("正向上下文命中:%s(+0.10)" % kw)
    for kw in NEGATIVE_CONTEXT:
        if kw in window:
            delta -= 0.15
            reasons.append("负向上下文命中:%s(-0.15)" % kw)
    return max(min(delta, 0.20), -0.30), reasons


# ---------------------------------------------------------------- 身份证

_ID_RE = re.compile(r"(?<![0-9A-Za-z])\d{17}[\dXx](?![0-9A-Za-z])")
_ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_ID_CHECK_CHARS = "10X98765432"
_PROVINCE_CODES = frozenset(
    "11 12 13 14 15 21 22 23 31 32 33 34 35 36 37 "
    "41 42 43 44 45 46 50 51 52 53 54 61 62 63 64 65 71 81 82 91".split()
)


def id_check_char(body17):
    """按 GB 11643-1999 计算第 18 位校验码。"""
    total = sum(int(d) * w for d, w in zip(body17, _ID_WEIGHTS))
    return _ID_CHECK_CHARS[total % 11]


def detect_id_card(text):
    candidates, rejected = [], []
    for m in _ID_RE.finditer(text):
        raw = m.group(0)
        value = raw.upper()
        if id_check_char(value[:17]) != value[17]:
            rejected.append(Rejection("id_card", m.start(), m.end(), raw,
                                      "checksum", "校验位不匹配(GB 11643-1999 加权求和)"))
            continue
        if value[:2] not in _PROVINCE_CODES:
            rejected.append(Rejection("id_card", m.start(), m.end(), raw,
                                      "prefix", "省级行政区划代码无效:%s" % value[:2]))
            continue
        try:
            birth = date(int(value[6:10]), int(value[10:12]), int(value[12:14]))
        except ValueError:
            rejected.append(Rejection("id_card", m.start(), m.end(), raw,
                                      "format", "出生日期无效:%s" % value[6:14]))
            continue
        if not (1900 <= birth.year <= date.today().year):
            rejected.append(Rejection("id_card", m.start(), m.end(), raw,
                                      "format", "出生年份超出合理范围:%d" % birth.year))
            continue
        confidence = 0.50 + 0.25 + 0.10 + 0.10
        reasons = ["正则命中18位身份证格式(+0.50)", "校验位通过(+0.25)",
                   "省级区划有效(+0.10)", "出生日期有效(+0.10)"]
        delta, ctx_reasons = _context_score(text, m.start(), m.end(), "id_card")
        confidence += delta
        reasons += ctx_reasons
        candidates.append(Candidate("id_card", m.start(), m.end(), raw, value,
                                    min(confidence, 1.0), reasons))
    return candidates, rejected


# ---------------------------------------------------------------- 银行卡

_CARD_RE = re.compile(r"(?<!\d)(?:\d[ \t\r\n\-]?){12,18}\d(?!\d)")

_IIN_TABLE = (
    ("62", "银联"), ("4", "Visa"),
    ("51", "MasterCard"), ("52", "MasterCard"), ("53", "MasterCard"),
    ("54", "MasterCard"), ("55", "MasterCard"),
    ("34", "Amex"), ("37", "Amex"), ("35", "JCB"),
    ("30", "Diners"), ("36", "Diners"), ("38", "Diners"),
    ("6011", "Discover"), ("65", "Discover"),
)


def _iin_brand(value):
    best = None
    for prefix, brand in _IIN_TABLE:
        if value.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, brand)
    return best[1] if best else None


def luhn_ok(digits):
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def luhn_check_digit(body):
    for d in "0123456789":
        if luhn_ok(body + d):
            return d
    raise ValueError("无法计算 Luhn 校验位")


def detect_bank_card(text):
    candidates, rejected = [], []
    for m in _CARD_RE.finditer(text):
        raw = m.group(0)
        value = re.sub(r"\D", "", raw)
        if len(set(value)) == 1:
            rejected.append(Rejection("bank_card", m.start(), m.end(), raw,
                                      "plausibility", "全部数字相同，不符合真实卡号分布"))
            continue
        if not luhn_ok(value):
            rejected.append(Rejection("bank_card", m.start(), m.end(), raw,
                                      "checksum", "Luhn 校验失败"))
            continue
        brand = _iin_brand(value)
        if brand is None:
            rejected.append(Rejection("bank_card", m.start(), m.end(), raw,
                                      "prefix", "IIN/BIN 前缀未知:%s" % value[:6]))
            continue
        confidence = 0.45 + 0.25 + 0.15
        reasons = ["正则命中13-19位数字串(+0.45)", "Luhn 校验通过(+0.25)",
                   "IIN 前缀有效:%s(+0.15)" % brand]
        if 16 <= len(value) <= 19:
            confidence += 0.05
            reasons.append("长度 %d 为常见卡号长度(+0.05)" % len(value))
        delta, ctx_reasons = _context_score(text, m.start(), m.end(), "bank_card")
        confidence += delta
        reasons += ctx_reasons
        candidates.append(Candidate("bank_card", m.start(), m.end(), raw, value,
                                    min(confidence, 1.0), reasons))
    return candidates, rejected


# ---------------------------------------------------------------- 手机号

_MOBILE_RE = re.compile(r"(?<!\d)1(?:[ \t\r\n\-]?\d){10}(?!\d)")
_ASC_SEQ = "01234567890123456789"
_DESC_SEQ = "98765432109876543210"


def detect_mobile(text):
    candidates, rejected = [], []
    for m in _MOBILE_RE.finditer(text):
        raw = m.group(0)
        value = re.sub(r"\D", "", raw)
        if value[1] not in "3456789":
            rejected.append(Rejection("mobile", m.start(), m.end(), raw,
                                      "prefix", "号段无效:1%sx 非国内手机号段" % value[1]))
            continue
        if value in _ASC_SEQ or value in _DESC_SEQ:
            rejected.append(Rejection("mobile", m.start(), m.end(), raw,
                                      "plausibility", "数字为连续序列，疑似伪造号码"))
            continue
        confidence = 0.50 + 0.15
        reasons = ["正则命中11位1开头数字串(+0.50)", "号段 1%sx 有效(+0.15)" % value[1]]
        if len(set(value)) <= 2:
            confidence -= 0.10
            reasons.append("数字重复度过高(-0.10)")
        else:
            confidence += 0.10
            reasons.append("数字分布合理(+0.10)")
        delta, ctx_reasons = _context_score(text, m.start(), m.end(), "mobile")
        confidence += delta
        reasons += ctx_reasons
        candidates.append(Candidate("mobile", m.start(), m.end(), raw, value,
                                    min(confidence, 1.0), reasons))
    return candidates, rejected


DETECTORS = (
    ("id_card", detect_id_card),
    ("bank_card", detect_bank_card),
    ("mobile", detect_mobile),
)
