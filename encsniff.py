"""encsniff: 文本编码嗅探与无损转换（仅 Python 标准库）。

支持编码:
  - UTF-8（含 BOM）
  - UTF-16 LE / BE（含或不含 BOM）
  - GBK（中文单字节/双字节混合）
  - Windows-1252（西欧单字节）

设计原则: 宁可拒绝，不可猜错。
置信度不足时 sniff() 抛出 SniffError，异常中附带全部候选编码及得分，
绝不默认选一个继续处理。

非法字节序列策略由调用方显式选择:
  - errors="strict"  : 抛出 UnicodeDecodeError
  - errors="replace" : 以 U+FFFD 替换，不丢字节位置信息
两种策略都不会静默丢数据；传入其他值直接抛 ValueError。
"""

from __future__ import annotations

import codecs
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

__all__ = [
    "SniffError",
    "Candidate",
    "SniffResult",
    "sniff",
    "convert",
    "SUPPORTED_ENCODINGS",
]

SUPPORTED_ENCODINGS = ("utf-8", "utf-16", "gbk", "cp1252")

# 接受阈值: 最高分不得低于 ACCEPT_MIN_SCORE，
# 且与次高分的差距不得低于 ACCEPT_MIN_MARGIN。
ACCEPT_MIN_SCORE = 0.60
ACCEPT_MIN_MARGIN = 0.15

# 嗅探时最多扫描的字节数（超大文件只采样头部，转换仍处理全量）。
_SAMPLE_LIMIT = 1 << 20  # 1 MiB

_ALLOWED_ERRORS = ("strict", "replace")


class SniffError(Exception):
    """置信度不足，拒绝判定。candidates 按得分降序列出全部候选。"""

    def __init__(self, message: str, candidates: List["Candidate"]):
        super().__init__(message)
        self.candidates = candidates


@dataclass
class Candidate:
    encoding: str
    score: float
    reason: str


@dataclass
class SniffResult:
    encoding: str
    confidence: float
    reasons: List[str] = field(default_factory=list)
    candidates: List[Candidate] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 各编码评分函数: 返回 Candidate 或 None（该编码不可能）
# ---------------------------------------------------------------------------

_BOMS: Tuple[Tuple[bytes, str], ...] = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)


def _match_bom(data: bytes) -> Optional[str]:
    for bom, encoding in _BOMS:
        if data.startswith(bom):
            return encoding
    return None


def _decode_sample(
    sample: bytes, encoding: str, allow_truncation: bool
) -> Optional[str]:
    """解码采样。allow_truncation 时容忍末尾被采样截断的少量字节。"""
    try:
        return sample.decode(encoding)
    except UnicodeDecodeError as exc:
        if allow_truncation and exc.start >= len(sample) - 4:
            try:
                return sample[: exc.start].decode(encoding)
            except UnicodeDecodeError:
                return None
        return None


def _utf8_candidate(data: bytes, allow_truncation: bool = False) -> Optional[Candidate]:
    if _decode_sample(data, "utf-8", allow_truncation) is None:
        return None
    seqs = sum(1 for b in data if 0xC0 <= b <= 0xF4)  # 多字节序列起始字节数
    if seqs == 0:
        return None  # 纯 ASCII 走单独分支
    # UTF-8 的序列结构（起始字节 + 连续字节）本身即强证据，
    # 但短样本证据有限，得分随序列数增长。
    score = 0.80 + 0.20 * min(1.0, seqs / 2)
    return Candidate("utf-8", score, f"合法 UTF-8 序列，含 {seqs} 个多字节字符")


def _gbk_candidate(data: bytes, allow_truncation: bool = False) -> Optional[Candidate]:
    high = sum(1 for b in data if b >= 0x80)
    if high == 0:
        return None
    text = _decode_sample(data, "gbk", allow_truncation)
    if text is None:
        return None
    pairs = high // 2
    if pairs == 0:
        return None
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    cjk_ratio = cjk / pairs
    # 双字节对数量是证据强度: 仅 1 对时可能是巧合（如 Latin-1 文本），
    # 需要 >=4 对才给满分。
    evidence = min(1.0, pairs / 4)
    score = (0.50 + 0.50 * cjk_ratio) * (0.50 + 0.50 * evidence)
    # 真实中文文本几乎不含 ASCII 字母；若"GBK 解码结果"里 ASCII 字母很多，
    # 更可能是西欧单字节文本被误当作 GBK（如 é+字母 恰好构成合法 GBK 对）。
    ascii_letters = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    letter_ratio = ascii_letters / max(len(text), 1)
    score *= 1.0 - 0.7 * min(1.0, letter_ratio * 2.5)
    return Candidate(
        "gbk", score, f"合法 GBK，{pairs} 个双字节对，CJK 占比 {cjk_ratio:.0%}"
    )


_CP1252_UNDEFINED = frozenset((0x81, 0x8D, 0x8F, 0x90, 0x9D))


def _cp1252_candidate(data: bytes) -> Optional[Candidate]:
    high = [b for b in data if b >= 0x80]
    if not high:
        return None
    if any(b in _CP1252_UNDEFINED for b in high):
        return None
    text = data.decode("cp1252")
    letters = sum(1 for ch in text if "À" <= ch <= "ÿ" and ch != "×" and ch != "÷")
    letter_ratio = letters / len(high)
    # 单字节编码几乎任何输入都"合法"，结构性证据最弱，基础分给低:
    # 短样本会被阈值拒绝，只有较多西欧字母证据时才被接受。
    score = 0.35 + 0.20 * letter_ratio + 0.15 * min(1.0, len(high) / 8)
    # 自然西欧文本高位字节稀疏（通常 <15%）。若高位字节占比过高，
    # "文本"更可能是双字节编码（或混合编码）被误读。
    high_ratio = len(high) / len(data)
    if high_ratio > 0.25:
        score *= max(0.2, 1.0 - (high_ratio - 0.25) * 2)
    return Candidate(
        "cp1252", score, f"可解码为 Windows-1252，{len(high)} 个高位字节"
    )


def _utf16_candidates(data: bytes, allow_truncation: bool = False) -> List[Candidate]:
    if len(data) < 4:
        return []
    sample = data[:4096]
    pairs = len(sample) // 2
    if pairs == 0:
        return []
    even_nulls = sample[0::2].count(0)
    odd_nulls = sample[1::2].count(0)
    results: List[Candidate] = []
    for encoding, nulls, other in (
        ("utf-16-le", odd_nulls, even_nulls),
        ("utf-16-be", even_nulls, odd_nulls),
    ):
        if nulls / pairs > 0.25 and other / pairs < 0.05:
            text = _decode_sample(data, encoding, allow_truncation)
            if text is None:
                continue
            printable = sum(1 for ch in text if ch.isprintable() or ch in "\r\n\t")
            if printable / max(len(text), 1) > 0.95:
                results.append(
                    Candidate(
                        encoding,
                        0.95,
                        f"无 BOM，但 NUL 字节分布符合 {encoding}（{nulls}/{pairs}）",
                    )
                )
    if results:
        return results
    # 无 NUL 特征的情形: 纯 CJK 的 UTF-16 高低字节均非零。
    # 特征: 高字节聚集于 CJK 码位高位区间 [0x4E, 0x9F]，且低字节有相当
    # 比例 >= 0x80（CJK 低字节近似均匀分布；西欧文本两个字节几乎全是
    # ASCII，可借此排除）。
    # 与 GBK 存在结构性歧义，故得分低于 GBK 满分，仅在 GBK 不合法时胜出。
    if len(data) % 2 == 0:
        for encoding, high_bytes, low_bytes in (
            ("utf-16-le", data[1::2], data[0::2]),
            ("utf-16-be", data[0::2], data[1::2]),
        ):
            if not high_bytes:
                continue
            cluster = sum(1 for b in high_bytes if 0x4E <= b <= 0x9F) / len(high_bytes)
            low_spread = sum(1 for b in low_bytes if b >= 0x80) / len(low_bytes)
            if cluster <= 0.6 or low_spread <= 0.3:
                continue
            text = _decode_sample(data, encoding, allow_truncation)
            if not text:
                continue
            results.append(
                Candidate(
                    encoding,
                    0.45 + 0.35 * cluster,
                    f"无 BOM/NUL 特征，{encoding} 高字节 {cluster:.0%} 落于 CJK 区间",
                )
            )
    return results


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------

def _normalize(encoding: str) -> str:
    return {"utf-8-sig": "utf-8"}.get(encoding, encoding)


def _mixed_check(data: bytes) -> Optional[str]:
    """低置信度时的分段一致性检查: 四等分后各自嗅探，若不同分段得出
    不同的高置信编码，说明输入很可能是多种编码拼接而成。"""
    if len(data) < 64:
        return None
    seg = len(data) // 4
    seg += seg & 1  # 保持偶数边界，避免切断 UTF-16 单元
    found = set()
    utf8_flags = []
    for i in range(4):
        part = data[i * seg:(i + 1) * seg if i < 3 else len(data)]
        if not part or all(b < 0x80 for b in part):
            continue  # 纯 ASCII 分段无法提供判别信息
        utf8_flags.append(_decode_sample(part, "utf-8", False) is not None)
        try:
            found.add(_normalize(sniff(part, _check_mixed=False).encoding))
        except SniffError:
            continue  # 分段自身不可判定（可能恰好在编码边界上），忽略
    # 一部分分段是合法 UTF-8 而另一部分不是: 必为拼接。
    if utf8_flags and any(utf8_flags) and not all(utf8_flags):
        return "部分分段是合法 UTF-8 而另一些不是，疑似混合编码"
    if len(found) > 1:
        return f"不同分段判定不一致（{' / '.join(sorted(found))}），疑似混合编码"
    return None


def sniff(
    data: bytes, _sample_limit: int = _SAMPLE_LIMIT, _check_mixed: bool = True
) -> SniffResult:
    """嗅探 data 的编码。

    成功返回 SniffResult；置信度不足抛出 SniffError（携带候选列表）。
    """
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("sniff() 需要 bytes 输入")

    # 空文件: 任何编码解码结果相同，直接安全返回。
    if len(data) == 0:
        return SniffResult(
            "utf-8", 1.0, ["空输入：所有编码解码结果一致"], []
        )

    # BOM 是确定性证据。
    bom_encoding = _match_bom(data)
    if bom_encoding is not None:
        return SniffResult(
            bom_encoding,
            1.0,
            [f"检测到 BOM，编码确定为 {bom_encoding}"],
            [Candidate(bom_encoding, 1.0, "BOM 签名匹配")],
        )

    sample = bytes(data[:_sample_limit])

    # 纯 ASCII: 所有 ASCII 兼容编码解码结果逐字节一致，选择无风险。
    if all(b < 0x80 for b in sample):
        return SniffResult(
            "utf-8",
            1.0,
            ["纯 ASCII：所有 ASCII 兼容编码解码结果一致"],
            [Candidate("utf-8", 1.0, "纯 ASCII")],
        )

    candidates: List[Candidate] = []
    truncated = len(data) > _sample_limit
    for cand in (
        _utf8_candidate(sample, truncated),
        _gbk_candidate(sample, truncated),
        _cp1252_candidate(sample),
    ):
        if cand is not None:
            candidates.append(cand)
    candidates.extend(_utf16_candidates(sample, truncated))
    candidates.sort(key=lambda c: c.score, reverse=True)

    if not candidates:
        raise SniffError(
            "没有任何候选编码能合法解码输入（可能含非法字节或为未支持的编码）",
            [],
        )

    top = candidates[0]
    second = candidates[1] if len(candidates) > 1 else None
    margin = top.score - (second.score if second else 0.0)
    confidence = top.score if second is None else top.score - 0.5 * second.score

    if top.score < ACCEPT_MIN_SCORE or margin < ACCEPT_MIN_MARGIN:
        detail = ", ".join(f"{c.encoding}={c.score:.2f}" for c in candidates)
        raise SniffError(
            f"置信度不足，拒绝判定（候选: {detail}）", candidates
        )

    # GBK/cp1252 结构性证据弱（字节组合巧合多），即使单候选满分也要
    # 做分段一致性检查；UTF-8/UTF-16 结构自校验强，高置信时可跳过。
    weak = top.encoding in ("gbk", "cp1252")
    if _check_mixed and (confidence < 0.95 or weak):
        mixed = _mixed_check(bytes(data[:_sample_limit]))
        if mixed is not None:
            raise SniffError(f"拒绝判定: {mixed}", candidates)

    reasons = [c.reason for c in candidates]
    reasons.append(
        f"接受 {top.encoding}: 得分 {top.score:.2f}，与次优差距 {margin:.2f}"
    )
    return SniffResult(top.encoding, confidence, reasons, candidates)


def convert(
    data: bytes,
    target_encoding: str = "utf-8",
    errors: str = "strict",
    source_encoding: Optional[str] = None,
) -> bytes:
    """把 data 从嗅探到的（或指定的）源编码转换为 target_encoding。

    errors 必须显式为 "strict" 或 "replace":
      - "strict"  : 遇非法字节序列抛 UnicodeDecodeError
      - "replace" : 以 U+FFFD 替换非法序列，不静默丢数据
    """
    if errors not in _ALLOWED_ERRORS:
        raise ValueError(
            f"errors 必须显式为 {_ALLOWED_ERRORS} 之一，不允许静默丢数据的策略"
        )
    if source_encoding is None:
        source_encoding = sniff(data).encoding
    text = data.decode(source_encoding, errors=errors)
    return text.encode(target_encoding, errors=errors)
