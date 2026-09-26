"""数据类型定义：候选、判定、命中、否定与冲突。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Candidate:
    """正则初筛得到的候选串。"""

    type: str
    start: int
    end: int
    raw: str          # 原文片段（可能含分隔符）
    normalized: str   # 去除分隔符后的纯内容


@dataclass
class Verdict:
    """校验环节对候选的判定结果。"""

    ok: bool
    score: float                        # 校验阶段得分（未含上下文调整）
    reasons: list[str] = field(default_factory=list)
    reject_reason: str | None = None    # 否定原因（ok=False 时必填）


@dataclass
class Scored:
    """通过校验、带最终置信度的候选（可能因阈值或冲突未成为命中）。"""

    type: str
    start: int
    end: int
    raw: str
    normalized: str
    score: float
    reasons: list[str]


@dataclass
class Match:
    """最终输出的命中。"""

    type: str
    start: int
    end: int
    raw: str
    normalized: str
    score: float
    reasons: list[str]


@dataclass
class Rejection:
    """被校验/阈值环节否定的候选及原因。"""

    type: str
    start: int
    end: int
    raw: str
    reason: str


@dataclass
class Conflict:
    """重叠命中冲突记录。"""

    kept_type: str
    kept_span: tuple[int, int]
    kept_score: float
    dropped_type: str
    dropped_span: tuple[int, int]
    dropped_score: float
    rule: str


@dataclass
class ScanResult:
    matches: list[Match]
    rejected: list[Rejection]
    conflicts: list[Conflict]
    scored: list[Scored]      # 所有通过校验的候选（含低于阈值/冲突被丢弃的）
    text_len: int
    elapsed_ms: float
