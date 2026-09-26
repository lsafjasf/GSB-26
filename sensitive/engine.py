"""敏感词匹配引擎：Aho-Corasick + 归一化 + 误伤控制。"""

from __future__ import annotations

from dataclasses import dataclass

from .ahocorasick import AhoCorasick
from .normalize import DEFAULT_CONFIG, NormalizeConfig, Normalized, Normalizer


@dataclass(frozen=True)
class Hit:
    """一次命中。

    Attributes:
        word: 词典里的原始词形（未归一化的插入形式；若多个原始词形归一化
            后相同，保留第一个插入的）。
        start: 原文起始码点偏移（0 基，含）。
        end: 原文结束码点偏移（0 基，不含）。命中原文片段为
            ``text[start:end]``，其中可能包含被归一化删除的字符。
        norm_start / norm_end: 归一化文本上的区间，供调试。
    """

    word: str
    start: int
    end: int
    norm_start: int
    norm_end: int


def ascii_word_char(ch: str) -> bool:
    """默认词字符判定：ASCII 字母数字下划线。

    中文/CJK 不视为词字符，因此「我爱天安门」中的「天安门」照常命中，
    而 ``scat`` 不会在 ``cat`` 位于 ``scatter`` 里时误报（边界模式下）。
    如需 Unicode 词字符语义，可传入 ``lambda c: c.isalnum() or c == '_'``。
    """
    return ("a" <= ch <= "z") or ("A" <= ch <= "Z") or ("0" <= ch <= "9") or ch == "_"


class SensitiveEngine:
    """敏感词多模式匹配引擎。

    Args:
        words: 敏感词列表。
        whitelist: 白名单词列表；白名单词的归一化命中区间若 **完全覆盖**
            某个敏感词命中区间，则该命中被放行（白名单允许长于敏感词）。
        config: 归一化配置，文本与词典共用同一配置。
        use_boundary: 是否启用词边界判定。启用后，仅当命中区间在归一化
            文本中两侧（跳过被删除字符后）都不紧贴 ``is_word_char`` 字符时
            才上报。
        is_word_char: 词字符判定函数（作用于单个码点）。
    """

    def __init__(
        self,
        words: list[str] | tuple[str, ...],
        whitelist: list[str] | tuple[str, ...] = (),
        config: NormalizeConfig = DEFAULT_CONFIG,
        use_boundary: bool = False,
        is_word_char=ascii_word_char,
    ) -> None:
        self.normalizer = Normalizer(config)
        self.config = config
        self.use_boundary = use_boundary
        self.is_word_char = is_word_char

        # 归一化词典；归一化后相同的词保留第一个插入的原始词形。
        self._id_to_word: list[str] = []
        norm_to_id: dict[str, int] = {}
        ac = AhoCorasick()
        skipped: list[str] = []
        for raw in words:
            norm = self.normalizer.normalize_pattern(raw)
            if not norm:
                skipped.append(raw)
                continue
            pid = norm_to_id.get(norm)
            if pid is None:
                pid = len(self._id_to_word)
                norm_to_id[norm] = pid
                self._id_to_word.append(raw)
                ac.add(norm, pid)
        ac.build()
        self._ac = ac
        self._lengths: list[int] = [len(self.normalizer.normalize_pattern(w)) for w in self._id_to_word]
        self.skipped_words: tuple[str, ...] = tuple(skipped)

        # 白名单自动机（归一化后的词，同样去重）
        self._whitelist_ac: AhoCorasick | None = None
        wl_norms = {
            n
            for n in (self.normalizer.normalize_pattern(w) for w in whitelist)
            if n
        }
        if wl_norms:
            wl_ac = AhoCorasick()
            wl_lengths: list[int] = []
            for idx, norm in enumerate(sorted(wl_norms)):
                wl_ac.add(norm, idx)
                wl_lengths.append(len(norm))
            wl_ac.build()
            self._whitelist_ac = wl_ac
            self._whitelist_lengths = wl_lengths

    @property
    def word_count(self) -> int:
        """实际生效（归一化后非空且去重）的敏感词数量。"""
        return len(self._id_to_word)

    @property
    def node_count(self) -> int:
        return self._ac.node_count

    # -- 边界判定 ---------------------------------------------------------

    def _passes_boundary(self, norm_text: str, start: int, end: int) -> bool:
        """在归一化文本上做边界判定。

        左侧：``start == 0`` 或前一码点不是词字符。
        右侧：``end == len`` 或后一码点不是词字符。
        判定基于归一化后的相邻字符，因此 ``a\\u200bc`` 这类被零宽字符
        隔开的片段，在删除零宽字符后视为相邻（边界生效），与匹配口径一致。
        """
        if not self.use_boundary:
            return True
        if start > 0 and self.is_word_char(norm_text[start - 1]):
            return False
        if end < len(norm_text) and self.is_word_char(norm_text[end]):
            return False
        return True

    @staticmethod
    def _covered_by_whitelist(
        intervals: list[tuple[int, int]],
        start: int,
        end: int,
    ) -> bool:
        """intervals 为按起点排序的白名单区间列表，判断 [start,end) 是否被
        某个白名单区间完整覆盖。intervals 通常很少，线性扫描即可。"""
        for ws, we in intervals:
            if ws > start:
                break
            if we >= end:
                return True
        return False

    # -- 主入口 -----------------------------------------------------------

    def find_all(self, text: str) -> list[Hit]:
        """扫描全文，返回全部命中，排序规则：

        1. 按原文起点 ``start`` 升序；
        2. 起点相同按原文终点 ``end`` 升序（短词在前）；
        3. 起止都相同按词形字典序（归一化后同形只会有一个词，此条仅为稳定）。

        返回 **全部重叠命中**：同一起点多个词、前缀包含、后缀包含均上报，
        不去重、不剪枝。需要“同起点只取最长”可由调用方自行过滤。
        """
        normalized: Normalized = self.normalizer.normalize(text)
        norm_text = normalized.text
        n = len(norm_text)

        # 白名单：扫描一次，收集全部区间（含重叠）并按起点排序。
        wl_intervals: list[tuple[int, int]] = []
        if self._whitelist_ac is not None:
            wl_lengths = self._whitelist_lengths  # type: ignore[attr-defined]
            for w_end, wid in self._whitelist_ac.scan(norm_text):
                wl_intervals.append((w_end - wl_lengths[wid], w_end))
            wl_intervals.sort()

        candidates: list[tuple[int, int, int]] = []  # (norm_start, norm_end, pid)
        words = self._id_to_word
        lengths = self._lengths
        passes_boundary = self._passes_boundary
        for end, pid in self._ac.scan(norm_text):
            start = end - lengths[pid]
            if not passes_boundary(norm_text, start, end):
                continue
            if wl_intervals and self._covered_by_whitelist(wl_intervals, start, end):
                continue
            candidates.append((start, end, pid))

        hits: list[Hit] = []
        seen: set[tuple[int, int, int]] = set()
        for nstart, nend, pid in candidates:
            key = (nstart, nend, pid)
            if key in seen:
                continue
            seen.add(key)
            ostart, oend = normalized.to_orig_span(nstart, nend)
            hits.append(
                Hit(
                    word=words[pid],
                    start=ostart,
                    end=oend,
                    norm_start=nstart,
                    norm_end=nend,
                )
            )
        hits.sort(key=lambda h: (h.start, h.end, h.word))
        return hits
