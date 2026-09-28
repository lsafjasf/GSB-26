"""扫描引擎：规范化 -> 候选生成 -> 校验 -> 上下文调整 -> 阈值过滤 -> 冲突消解。

扫描前先剔除零宽与格式控制字符（Unicode Cf），命中位置再映射回原文偏移，
因此报告中的 start/end/raw 始终指向原文。

冲突消解规则（确定性）：
  1. 置信度高者优先；
  2. 置信度相同，跨度更长者优先；
  3. 仍相同，按类型优先级 id_card > bank_card > phone > passport；
  4. 再相同，起始位置靠前者优先。
被丢弃的重叠候选记录到 ScanResult.conflicts。
"""
from __future__ import annotations

import time

from .detectors import (
    BankCardDetector,
    Detector,
    IdCardDetector,
    PassportDetector,
    PhoneDetector,
)
from .normalize import normalize_text, span_to_original
from .types import Conflict, Match, Rejection, ScanResult, Scored

# 上下文窗口（命中位置前后各取多少字符）
CONTEXT_WINDOW = 24

_POSITIVE_KW = (
    "身份证", "身份", "证件号", "证件", "银行卡", "卡号", "信用卡", "借记卡",
    "储蓄卡", "手机号", "手机", "联系电话", "电话", "联系方式", "护照", "通行证",
)
_NEGATIVE_KW = (
    "订单", "单号", "编号", "流水", "批次", "版本", "货号", "数量", "单价",
    "金额", "价格", "日期", "时间戳", "序号", "编码", "条码", "快递", "物流",
)

_POS_BONUS = 0.10
_NEG_PENALTY = 0.15

_TYPE_PRIORITY = {"id_card": 0, "bank_card": 1, "phone": 2, "passport": 3}

CONFLICT_RULE = "置信度降序 > 跨度降序 > 类型优先级(id_card>bank_card>phone>passport) > 起始位置升序"


def _context_adjust(text: str, start: int, end: int) -> tuple[float, list[str]]:
    lo = max(0, start - CONTEXT_WINDOW)
    hi = min(len(text), end + CONTEXT_WINDOW)
    window = text[lo:hi]
    delta = 0.0
    notes: list[str] = []
    pos_hits = [kw for kw in _POSITIVE_KW if kw in window]
    neg_hits = [kw for kw in _NEGATIVE_KW if kw in window]
    if pos_hits:
        delta += _POS_BONUS
        notes.append(f"上下文含敏感数据指示词 {pos_hits}(+{_POS_BONUS:.2f})")
    if neg_hits:
        delta -= _NEG_PENALTY
        notes.append(f"上下文含非敏感编号指示词 {neg_hits}(-{_NEG_PENALTY:.2f})")
    return delta, notes


class Scanner:
    def __init__(self, threshold: float = 0.5, detectors: list[Detector] | None = None,
                 normalize: bool = True):
        self.threshold = threshold
        self.normalize = normalize
        self.detectors = detectors or [
            IdCardDetector(), BankCardDetector(), PhoneDetector(), PassportDetector(),
        ]

    def scan(self, text: str) -> ScanResult:
        t0 = time.perf_counter()
        if self.normalize:
            work, index_map = normalize_text(text)
        else:
            work, index_map = text, list(range(len(text)))

        scored: list[Scored] = []
        rejected: list[Rejection] = []

        for det in self.detectors:
            for cand in det.find(work):
                verdict = det.validate(cand)
                o_start, o_end = span_to_original(index_map, cand.start, cand.end)
                raw = text[o_start:o_end]
                if not verdict.ok:
                    rejected.append(Rejection(
                        cand.type, o_start, o_end, raw,
                        f"校验否定：{verdict.reject_reason}",
                    ))
                    continue
                delta, notes = _context_adjust(work, cand.start, cand.end)
                score = max(0.0, min(1.0, verdict.score + delta))
                scored.append(Scored(
                    cand.type, o_start, o_end, raw, cand.normalized,
                    score, verdict.reasons + notes,
                ))

        # 阈值过滤：低于阈值的候选记入否定列表，便于审计
        accepted: list[Scored] = []
        for c in scored:
            if c.score >= self.threshold:
                accepted.append(c)
            else:
                rejected.append(Rejection(
                    c.type, c.start, c.end, c.raw,
                    f"置信度 {c.score:.2f} 低于阈值 {self.threshold:.2f}",
                ))

        # 冲突消解：按确定性规则排序后贪心选取不重叠候选。
        # 用位置分桶索引已接受的命中，避免 O(n^2) 的两两比较
        # （此处 start/end 已是原文偏移，冲突判定与输出同一坐标系）。
        accepted.sort(key=lambda c: (
            -c.score, -(c.end - c.start), _TYPE_PRIORITY.get(c.type, 99), c.start,
        ))
        bucket_size = 64
        buckets: dict[int, list[Match]] = {}
        matches: list[Match] = []
        conflicts: list[Conflict] = []
        for c in accepted:
            winner = None
            for b in range(c.start // bucket_size, (c.end - 1) // bucket_size + 1):
                for m in buckets.get(b, ()):
                    if c.start < m.end and m.start < c.end:
                        winner = m
                        break
                if winner is not None:
                    break
            if winner is not None:
                conflicts.append(Conflict(
                    kept_type=winner.type, kept_span=(winner.start, winner.end),
                    kept_score=winner.score,
                    dropped_type=c.type, dropped_span=(c.start, c.end),
                    dropped_score=c.score, rule=CONFLICT_RULE,
                ))
                continue
            match = Match(c.type, c.start, c.end, c.raw, c.normalized,
                          c.score, c.reasons)
            matches.append(match)
            for b in range(match.start // bucket_size, (match.end - 1) // bucket_size + 1):
                buckets.setdefault(b, []).append(match)

        matches.sort(key=lambda m: m.start)
        elapsed = (time.perf_counter() - t0) * 1000.0
        return ScanResult(matches, rejected, conflicts, scored, len(text), elapsed)


def scan_text(text: str, threshold: float = 0.5) -> ScanResult:
    return Scanner(threshold=threshold).scan(text)


def normalization_diff(text: str, threshold: float = 0.5) -> dict:
    """对比同一份文本在"规范化"与"不规范化"两种扫描下的命中集合。

    返回 {"recovered": [...], "lost": [...]}：
      - recovered：仅规范化后命中的条目（剔除零宽/格式控制字符后找回的命中）
      - lost：仅不规范化时命中的条目（规范化后消失的命中，理论上应为空）
    条目为 (type, start, end, normalized) 四元组，位置均为原文偏移，可逐条核对。
    """
    def key_set(result: ScanResult) -> set[tuple[str, int, int, str]]:
        return {(m.type, m.start, m.end, m.normalized) for m in result.matches}

    with_norm = key_set(Scanner(threshold=threshold, normalize=True).scan(text))
    without_norm = key_set(Scanner(threshold=threshold, normalize=False).scan(text))
    return {
        "recovered": sorted(with_norm - without_norm, key=lambda t: (t[1], t[0])),
        "lost": sorted(without_norm - with_norm, key=lambda t: (t[1], t[0])),
    }
