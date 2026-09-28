"""文本归一化与位置映射。

所有归一化都在 Unicode 码点层面进行，且保持 **单码点 -> 单码点/删除**
的映射关系（``str.casefold`` 的多码点展开会被跳过，见下），因此每个归一化
后的码点都能精确对应回原文中的一个码点偏移。

归一化项目（均可独立开关）：

* ``casefold``   大小写折叠（``str.casefold``，比 ``lower`` 更彻底，
  例如 ß -> ss 的情形会因一对多而被跳过，不做折叠）。
* ``width``      全半角转换：全角 ASCII（U+FF01..U+FF5E）-> 半角，
  全角空格 U+3000 -> 普通空格 U+0020。半角片假名不在处理范围内。
* ``t2s``        繁→简 **严格单字 1:1** 映射（数据见
  ``data/t2s_chars.txt``，源自 OpenCC，只收录单字且目标也是单字的
  条目，3151 条）。一对多/词级繁简转换（如「乾→干/乾」「發/髮」）不在
  表内，保持原字；因此仍是单码点变换，位置映射成立。
* ``homophone``  同音折叠：把同音字（带声调、拼音相同）统一折叠到该
  同音组的代表字（数据见 ``data/homophone_groups.txt``，753 组 / 3372
  个 GB2312 一级常用字，多音字剔除）。误伤面很大，**默认关闭**，仅适合
  对少量重点词做对抗拆字时开启，详见 README「同音折叠的边界」。
  可用 ``homophone_table`` 传入自定义映射（``{原字: 折叠目标}``）。
* ``whitespace`` 空白处理，三种模式：
  - ``"keep"``     原样保留（仅在 width 开启时 U+3000 变空格）；
  - ``"collapse"`` 所有 Unicode 空白折叠为一个 ASCII 空格，连续多个
    合并为一个（默认）；
  - ``"remove"``   删除所有空白（允许用 "敏感　词"、"敏 感 词" 绕过的
    场景，但会提高误伤面，见 README）。
* ``zero_width`` 删除零宽字符：U+200B/U+200C/U+200D/U+FEFF/U+180E
  以及 U+2060..U+2064（``word_joiner``、``invisible separator`` 等）。
  组合用的变体选择符 U+FE00..U+FE0F 不属于零宽字符，不删除。

注意：选项的组合是确定的，词典与文本必须用同一套配置归一化，本模块的
``Normalizer`` 保证这一点。

单码点变换固定按以下顺序串接：零宽删除 → 全半角 → 繁→简 → 同音折叠 →
空白处理 → 大小写折叠；每一步都只做 1:1 替换或删除，因此全链路保持
单码点对应。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

WS_KEEP = "keep"
WS_COLLAPSE = "collapse"
WS_REMOVE = "remove"

_ZERO_WIDTH = (
    0x200B,  # ZERO WIDTH SPACE
    0x200C,  # ZERO WIDTH NON-JOINER
    0x200D,  # ZERO WIDTH JOINER
    0x2060,  # WORD JOINER
    0xFEFF,  # ZERO WIDTH NO-BREAK SPACE / BOM
    0x180E,  # MONGOLIAN VOWEL SEPARATOR
    *range(0x2061, 0x2065),  # INVISIBLE TIMES/SEPARATORS/PLUS
)
_ZERO_WIDTH_SET = frozenset(_ZERO_WIDTH)

_DATA_DIR = Path(__file__).resolve().parent / "data"
_T2S_TABLE_FILE = _DATA_DIR / "t2s_chars.txt"
_HOMOPHONE_TABLE_FILE = _DATA_DIR / "homophone_groups.txt"


def _load_t2s_table() -> dict[str, str]:
    """加载繁→简严格 1:1 单字表（每行两个汉字）。"""
    table: dict[str, str] = {}
    with _T2S_TABLE_FILE.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if len(line) == 2:
                table[line[0]] = line[1]
    return table


def _load_homophone_table() -> dict[str, str]:
    """加载同音折叠表（每行一组，首列为折叠目标，其余字映射到首列）。"""
    table: dict[str, str] = {}
    with _HOMOPHONE_TABLE_FILE.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if len(line) >= 2:
                target = line[0]
                for ch in line[1:]:
                    table[ch] = target
    return table


@dataclass(frozen=True)
class NormalizeConfig:
    casefold: bool = True
    width: bool = True
    # 繁→简单字 1:1（默认开启：覆盖面大、语义碰撞少；详见 README）
    t2s: bool = True
    # 同音折叠（默认关闭：误伤面大，仅重点词对抗场景开启）
    homophone: bool = False
    # "keep" | "collapse" | "remove"
    whitespace: str = WS_COLLAPSE
    zero_width: bool = True
    # 自定义同音映射（仅在 homophone=True 时生效）；None 表示用随包字表
    homophone_table: dict[str, str] | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if self.whitespace not in (WS_KEEP, WS_COLLAPSE, WS_REMOVE):
            raise ValueError(f"unknown whitespace mode: {self.whitespace!r}")


# 常用预设：默认配置（宽松归一化，适合一般文本审核）
DEFAULT_CONFIG = NormalizeConfig()
# 不归一化：原样匹配
RAW_CONFIG = NormalizeConfig(
    casefold=False,
    width=False,
    t2s=False,
    homophone=False,
    whitespace=WS_KEEP,
    zero_width=False,
)


@dataclass(frozen=True)
class Normalized:
    """归一化结果。

    Attributes:
        text: 归一化后的文本。
        orig_pos: ``orig_pos[i]`` 是归一化文本第 i 个码点在原文中的偏移；
            长度恒等于 ``len(text)``。
        orig_len: 原文码点数。
    """

    text: str
    orig_pos: tuple[int, ...]
    orig_len: int

    def to_orig_span(self, norm_start: int, norm_end: int) -> tuple[int, int]:
        """把归一化文本上的半开区间 [start, end) 映射回原文半开区间。

        约定：命中归一化区间 [s, e)（含 s，不含 e），对应原文区间
        ``[orig_pos[s], orig_pos[e-1]+1)``，即覆盖首字到尾字在原文中的
        完整码点范围（包含中间被删掉的零宽字符/空白）。空区间按空区间处理。
        """
        if norm_start >= norm_end:
            return (0, 0)
        start = self.orig_pos[norm_start]
        end = self.orig_pos[norm_end - 1] + 1
        return start, end


class Normalizer:
    """按固定 :class:`NormalizeConfig` 归一化文本与敏感词。"""

    # 随包静态字表惰性加载、进程内共享（不可变映射，只读安全）
    _t2s_table: dict[str, str] | None = None
    _homophone_table: dict[str, str] | None = None

    def __init__(self, config: NormalizeConfig = DEFAULT_CONFIG) -> None:
        self.config = config
        if config.homophone:
            if config.homophone_table is not None:
                self._homophone = config.homophone_table
            else:
                if Normalizer._homophone_table is None:
                    Normalizer._homophone_table = _load_homophone_table()
                self._homophone = Normalizer._homophone_table
        else:
            self._homophone = {}
        if config.t2s:
            if Normalizer._t2s_table is None:
                Normalizer._t2s_table = _load_t2s_table()
            self._t2s = Normalizer._t2s_table
        else:
            self._t2s = {}

    # -- 单码点变换 -------------------------------------------------------

    def _fold_char(self, ch: str) -> str | None:
        """单码点变换；返回 None 表示删除；返回长度为 1 的串表示替换。"""
        cp = ord(ch)
        cfg = self.config

        if cfg.zero_width and cp in _ZERO_WIDTH_SET:
            return None

        if cfg.width:
            if 0xFF01 <= cp <= 0xFF5E:
                ch = chr(cp - 0xFEE0)
                cp = ord(ch)
            elif cp == 0x3000:
                ch = " "
                cp = 0x20

        # 繁→简（严格 1:1 单字表，未收录的字保持原样）
        if cfg.t2s:
            ch = self._t2s.get(ch, ch)

        # 同音折叠（组内字 -> 代表字；未收录的字保持原样）
        if cfg.homophone:
            ch = self._homophone.get(ch, ch)

        if cfg.whitespace != WS_KEEP and ch.isspace():
            if cfg.whitespace == WS_REMOVE:
                return None
            return " "  # collapse：折叠成 ASCII 空格，合并在调用处处理

        if cfg.casefold:
            folded = ch.casefold()
            # 只接受一对一的折叠（如 ß -> ss 保持原样，不折叠），
            # 保证归一化码点与原码点一一对应。
            if len(folded) == 1:
                return folded
        return ch

    # -- 文本入口 ---------------------------------------------------------

    def normalize(self, text: str) -> Normalized:
        chars: list[str] = []
        positions: list[int] = []
        prev_space = True  # 开头的空白折叠后不产生空格
        for idx, ch in enumerate(text):
            out = self._fold_char(ch)
            if out is None:
                continue
            if self.config.whitespace == WS_COLLAPSE and out == " ":
                if prev_space:
                    continue
                prev_space = True
            else:
                prev_space = out == " "
            chars.append(out)
            positions.append(idx)
        return Normalized("".join(chars), tuple(positions), len(text))

    def normalize_pattern(self, pattern: str) -> str:
        """归一化敏感词/白名单词（无位置映射需求）。

        归一化后为空串的词（例如词本身只有零宽字符）会返回空串，
        由调用方拒绝加入词典。
        """
        return self.normalize(pattern).text
