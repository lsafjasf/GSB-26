"""朴素逐词扫描参考实现（只用于对拍/自测，禁止用于生产）。

对每个词分别用 ``str.find`` 扫全文，时间复杂度 O(词数 × 文本长度)。
归一化、位置映射、边界、白名单口径与 :class:`SensitiveEngine` 完全一致，
从而可以逐命中比对两边的命中集合。
"""

from __future__ import annotations

from .engine import Hit, ascii_word_char
from .normalize import DEFAULT_CONFIG, NormalizeConfig, Normalizer


def naive_scan(
    norm_text: str,
    norm_words: list[str],
) -> list[tuple[int, int, int]]:
    """对归一化文本逐词 ``str.find``，返回 ``(norm_start, norm_end, pid)``
    的全部重叠出现（未排序）。"""
    results: list[tuple[int, int, int]] = []
    for pid, word in enumerate(norm_words):
        start = 0
        wlen = len(word)
        while True:
            idx = norm_text.find(word, start)
            if idx < 0:
                break
            results.append((idx, idx + wlen, pid))
            start = idx + 1  # 允许重叠
    return results


def naive_find_all(
    text: str,
    words: list[str] | tuple[str, ...],
    whitelist: list[str] | tuple[str, ...] = (),
    config: NormalizeConfig = DEFAULT_CONFIG,
    use_boundary: bool = False,
    is_word_char=ascii_word_char,
) -> list[Hit]:
    """与 :meth:`SensitiveEngine.find_all` 同口径的朴素实现。"""
    normalizer = Normalizer(config)
    normalized = normalizer.normalize(text)
    norm_text = normalized.text

    id_to_word: list[str] = []
    norm_words: list[str] = []
    seen: dict[str, int] = {}
    for raw in words:
        norm = normalizer.normalize_pattern(raw)
        if not norm or norm in seen:
            continue
        seen[norm] = len(id_to_word)
        id_to_word.append(raw)
        norm_words.append(norm)

    def passes_boundary(start: int, end: int) -> bool:
        if not use_boundary:
            return True
        if start > 0 and is_word_char(norm_text[start - 1]):
            return False
        if end < len(norm_text) and is_word_char(norm_text[end]):
            return False
        return True

    wl_norms = sorted(
        {n for n in (normalizer.normalize_pattern(w) for w in whitelist) if n}
    )
    wl_intervals: list[tuple[int, int]] = []
    for w in wl_norms:
        start = 0
        while True:
            idx = norm_text.find(w, start)
            if idx < 0:
                break
            wl_intervals.append((idx, idx + len(w)))
            start = idx + 1
    wl_intervals.sort()

    hits: list[Hit] = []
    for nstart, nend, pid in naive_scan(norm_text, norm_words):
        if not passes_boundary(nstart, nend):
            continue
        covered = any(ws <= nstart and we >= nend for ws, we in wl_intervals)
        if covered:
            continue
        ostart, oend = normalized.to_orig_span(nstart, nend)
        hits.append(
            Hit(
                word=id_to_word[pid],
                start=ostart,
                end=oend,
                norm_start=nstart,
                norm_end=nend,
            )
        )
    hits.sort(key=lambda h: (h.start, h.end, h.word))
    return hits
