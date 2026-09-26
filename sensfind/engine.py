"""扫描引擎：文本归一化、候选汇总、阈值过滤、重叠冲突消解。"""
from __future__ import annotations

from dataclasses import dataclass, field

from .detectors import DETECTORS, Candidate, Rejection

ZERO_WIDTH_CHARS = frozenset("\u200b\u200c\u200d\ufeff")  # 零宽字符，归一化时剔除

# 冲突消解时的类型优先级（越具体、校验越强的类型越优先）
TYPE_PRIORITY = {"id_card": 0, "bank_card": 1, "mobile": 2}

CONFLICT_RULE = (
    "置信度高者优先；并列时跨度长者优先；再并列时类型优先级 "
    "id_card > bank_card > mobile；再并列时起始位置靠前者优先"
)


@dataclass
class Finding:
    type: str
    start: int          # 原始文本中的偏移
    end: int
    raw: str            # 原始文本片段（可能含分隔符/零宽字符）
    value: str          # 归一化值
    confidence: float
    reasons: list = field(default_factory=list)

    def to_dict(self):
        return {
            "type": self.type, "start": self.start, "end": self.end,
            "raw": self.raw, "value": self.value,
            "confidence": round(self.confidence, 4), "reasons": self.reasons,
        }


@dataclass
class ScanResult:
    findings: list      # List[Finding]，按起始位置排序
    rejected: list      # List[dict]，被校验/阈值否定的候选及原因
    conflicts: list     # List[dict]，重叠消解记录
    stats: dict

    def to_dict(self):
        return {
            "findings": [f.to_dict() for f in self.findings],
            "rejected": self.rejected,
            "conflicts": self.conflicts,
            "stats": self.stats,
        }


def _normalize(text):
    """剔除零宽字符，返回 (归一化文本, 归一化偏移->原始偏移 映射)。"""
    chars, index_map = [], []
    for i, ch in enumerate(text):
        if ch in ZERO_WIDTH_CHARS:
            continue
        chars.append(ch)
        index_map.append(i)
    return "".join(chars), index_map


def _to_original_span(index_map, start, end):
    return index_map[start], index_map[end - 1] + 1


class Engine:
    def __init__(self, threshold=0.6):
        self.threshold = threshold

    def scan(self, text):
        if not text:
            return ScanResult([], [], [], {"chars": 0, "candidates": 0})

        norm_text, index_map = _normalize(text)
        candidates, rejected = [], []
        for _, detect in DETECTORS:
            c, r = detect(norm_text)
            candidates.extend(c)
            rejected.extend(r)

        # 阈值过滤：通过校验但置信度不足的候选也记录否定原因
        accepted = []
        for cand in candidates:
            if cand.confidence < self.threshold:
                rejected.append(Rejection(
                    cand.type, cand.start, cand.end, cand.raw, "threshold",
                    "置信度 %.2f 低于阈值 %.2f" % (cand.confidence, self.threshold)))
            else:
                accepted.append(cand)

        # 重叠冲突消解：先按位置扫描出重叠连通分量（O(n log n)），
        # 分量内按确定性优先级排序后贪心选取不重叠候选。
        def priority_key(c):
            return (-c.confidence, -(c.end - c.start), c.start, TYPE_PRIORITY[c.type])

        accepted.sort(key=lambda c: c.start)
        components, current, current_end = [], [], -1
        for cand in accepted:
            if current and cand.start < current_end:
                current.append(cand)
                current_end = max(current_end, cand.end)
            else:
                if current:
                    components.append(current)
                current, current_end = [cand], cand.end
        if current:
            components.append(current)

        kept, conflicts = [], []
        for component in components:
            chosen = []
            for cand in sorted(component, key=priority_key):
                winner = next((k for k in chosen
                               if k.start < cand.end and cand.start < k.end), None)
                if winner is None:
                    chosen.append(cand)
                else:
                    conflicts.append({
                        "kept": {"type": winner.type, "value": winner.value,
                                 "confidence": round(winner.confidence, 4)},
                        "dropped": {"type": cand.type, "value": cand.value,
                                    "confidence": round(cand.confidence, 4)},
                        "rule": CONFLICT_RULE,
                    })
            kept.extend(chosen)

        findings = []
        for cand in kept:
            start, end = _to_original_span(index_map, cand.start, cand.end)
            findings.append(Finding(cand.type, start, end,
                                    text[start:end], cand.value,
                                    cand.confidence, cand.reasons))
        findings.sort(key=lambda f: f.start)

        rejected_out = []
        for r in rejected:
            start, end = _to_original_span(index_map, r.start, r.end)
            rejected_out.append({
                "type": r.type, "start": start, "end": end,
                "raw": text[start:end], "stage": r.stage, "reason": r.reason,
            })

        return ScanResult(findings, rejected_out, conflicts,
                          {"chars": len(text), "candidates": len(candidates)})
