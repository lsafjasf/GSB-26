"""区域化排序库（仅标准库）。

排序规则完全由 JSON 配置声明：
  - letters: 拉丁字符 -> [主级字母, 次级变音等级]
  - cjk:     汉字 -> {"pinyin": 主级拼音, "tone": 次级声调, "alt": [其他读音]}
  - symbols: 符号 -> 显式排序位次
  - numeric: 数字串是否按数值比较
  - case_first / unknown_position: 大小写次序、未知字符回退位置
  - uncovered_policy: 未覆盖字符处理策略
      "codepoint" 回退到码位（默认，配合 unknown_position 决定首尾）
      "error"     遇到未覆盖字符直接抛出 UncoveredCharError
      "fold"      按等价类折叠（NFKD 去变音 + casefold 后归入已覆盖字符）

排序键为三级结构 (主级, 次级, 三级)，逐级比较：
  主级: 字母/拼音/数值/符号位次
  次级: 变音符号等级 / 声调
  三级: 大小写（折叠字符排在同等价类原字符之后）

完整性检查 coverage_check() 扫描配置声明的映射表与 Unicode 字符类别，
报告未被任何规则覆盖的类别；结果含内容摘要（digest），可复算验证。
"""

import hashlib
import json
import unicodedata

TAG_SYMBOL = 0    # 符号（配置中显式给出位次）
TAG_NUMBER = 1    # 数字串（按数值）
TAG_ALPHA = 2     # 拉丁字母 / 汉字拼音
TAG_UNKNOWN_END = 9   # 未知字符回退到末尾
TAG_UNKNOWN_START = -1  # 未知字符回退到开头

POLICY_CODEPOINT = "codepoint"
POLICY_ERROR = "error"
POLICY_FOLD = "fold"
UNCOVERED_POLICIES = (POLICY_CODEPOINT, POLICY_ERROR, POLICY_FOLD)

CASE_FIRST_VALUES = ("lower", "upper")
UNKNOWN_POSITIONS = ("start", "end")

# Unicode 一般类别（二位码）-> 中文名，用于完整性报告
CATEGORY_NAMES = {
    "Lu": "大写字母", "Ll": "小写字母", "Lt": "标题大写字母",
    "Lm": "修饰字母", "Lo": "其他字母（汉字/假名等）",
    "Mn": "非间距组合符", "Mc": "间距组合符", "Me": "包围组合符",
    "Nd": "十进制数字", "Nl": "字母数字", "No": "其他数字",
    "Pc": "连接标点", "Pd": "破折标点", "Ps": "开标点", "Pe": "闭标点",
    "Pi": "初始引号", "Pf": "终止引号", "Po": "其他标点",
    "Sm": "数学符号", "Sc": "货币符号", "Sk": "修饰符号", "So": "其他符号",
    "Zs": "空格分隔符", "Zl": "行分隔符", "Zp": "段分隔符",
    "Cc": "控制字符", "Cf": "格式字符", "Co": "私用区",
}


class ConfigError(ValueError):
    """配置校验失败。"""


class UncoveredCharError(ValueError):
    """uncovered_policy="error" 时遇到未覆盖字符。"""

    def __init__(self, ch):
        self.char = ch
        super().__init__(
            "字符 U+%04X %r 未被任何规则覆盖（uncovered_policy='error'）"
            % (ord(ch), ch))


def validate_config(config):
    """校验配置，返回问题列表（空列表表示通过）。顺序确定，可复算。"""
    issues = []

    case_first = config.get("case_first", "lower")
    if case_first not in CASE_FIRST_VALUES:
        issues.append("case_first 必须是 %s，得到 %r"
                      % ("/".join(CASE_FIRST_VALUES), case_first))
    unknown_position = config.get("unknown_position", "end")
    if unknown_position not in UNKNOWN_POSITIONS:
        issues.append("unknown_position 必须是 %s，得到 %r"
                      % ("/".join(UNKNOWN_POSITIONS), unknown_position))
    policy = config.get("uncovered_policy", POLICY_CODEPOINT)
    if policy not in UNCOVERED_POLICIES:
        issues.append("uncovered_policy 必须是 %s，得到 %r"
                      % ("/".join(UNCOVERED_POLICIES), policy))

    owners = {}  # char -> 首次声明它的表名，用于跨表冲突检查

    def claim(ch, table):
        if len(ch) != 1:
            issues.append("%s 的键必须是单字符: %r" % (table, ch))
            return
        prev = owners.get(ch)
        if prev is not None:
            issues.append("字符 %r 同时出现在 %s 与 %s" % (ch, prev, table))
        else:
            owners[ch] = table

    for ch, entry in config.get("letters", {}).items():
        claim(ch, "letters")
        if not (isinstance(entry, (list, tuple)) and len(entry) == 2
                and isinstance(entry[0], str) and entry[0]):
            issues.append("letters[%r] 必须是 [主级字母, 变音等级]: %r"
                          % (ch, entry))
            continue
        if not isinstance(entry[1], int) or isinstance(entry[1], bool):
            issues.append("letters[%r] 的变音等级必须是整数: %r" % (ch, entry))

    seen_ranks = {}
    for ch, rank in config.get("symbols", {}).items():
        claim(ch, "symbols")
        if not isinstance(rank, int) or isinstance(rank, bool):
            issues.append("symbols[%r] 的位次必须是整数: %r" % (ch, rank))
            continue
        prev = seen_ranks.get(rank)
        if prev is not None:
            issues.append("symbols 位次 %d 被 %r 与 %r 重复占用"
                          % (rank, prev, ch))
        else:
            seen_ranks[rank] = ch

    for ch, entry in config.get("cjk", {}).items():
        claim(ch, "cjk")
        if not isinstance(entry, dict):
            issues.append("cjk[%r] 必须是对象: %r" % (ch, entry))
            continue
        pinyin = entry.get("pinyin")
        if not isinstance(pinyin, str) or not pinyin:
            issues.append("cjk[%r] 缺少非空 pinyin" % ch)
        tone = entry.get("tone", 0)
        if not isinstance(tone, int) or isinstance(tone, bool) \
                or not 0 <= tone <= 5:
            issues.append("cjk[%r] 的 tone 必须是 0..5 的整数: %r"
                          % (ch, tone))
        alt = entry.get("alt") or []
        if not isinstance(alt, list) \
                or any(not isinstance(a, str) for a in alt):
            issues.append("cjk[%r] 的 alt 必须是字符串列表: %r" % (ch, alt))

    return issues


class LocaleSorter:
    """按配置声明的规则生成排序键与比较器。"""

    def __init__(self, config):
        issues = validate_config(config)
        if issues:
            raise ConfigError("配置校验失败:\n  " + "\n  ".join(issues))

        self.name = config.get("name", "unnamed")
        self.numeric = bool(config.get("numeric", True))
        self.case_first = config.get("case_first", "lower")
        self.unknown_position = config.get("unknown_position", "end")
        self.uncovered_policy = config.get("uncovered_policy",
                                           POLICY_CODEPOINT)
        self._unknown_tag = (
            TAG_UNKNOWN_END if self.unknown_position == "end"
            else TAG_UNKNOWN_START
        )

        # 拉丁字母表: char -> (primary, secondary)
        self._letters = {}
        for ch, entry in config.get("letters", {}).items():
            self._letters[ch] = (entry[0], int(entry[1]))

        # 符号表: char -> 位次
        self._symbols = {}
        for ch, rank in config.get("symbols", {}).items():
            self._symbols[ch] = int(rank)

        # 汉字表: char -> (pinyin, tone)；多音字记录全部读音
        self._cjk = {}
        self.polyphones = {}
        for ch, entry in config.get("cjk", {}).items():
            pinyin = entry["pinyin"]
            tone = int(entry.get("tone", 0))
            self._cjk[ch] = (pinyin, tone)
            alt = list(entry.get("alt") or [])
            if alt:
                self.polyphones[ch] = ["%s%d" % (pinyin, tone)] + alt

        # 未覆盖字符统计: char -> 出现次数
        self._uncovered = {}

    @classmethod
    def from_file(cls, path):
        with open(path, "r", encoding="utf-8") as fh:
            return cls(json.load(fh))

    # ------------------------------------------------------------------ key

    def _case_rank(self, ch):
        if ch.islower():
            return 0 if self.case_first == "lower" else 1
        if ch.isupper():
            return 1 if self.case_first == "lower" else 0
        return 0

    @staticmethod
    def _is_ascii_digit(ch):
        return "0" <= ch <= "9"

    def _fold_base(self, ch):
        """NFKD 分解后去掉组合符并 casefold，得到等价类基字符。"""
        decomposed = unicodedata.normalize("NFKD", ch)
        base = "".join(c for c in decomposed if not unicodedata.combining(c))
        return base.casefold() or ch

    def _uncovered_entry(self, ch):
        """按 uncovered_policy 为未覆盖字符生成键条目。"""
        self._uncovered[ch] = self._uncovered.get(ch, 0) + 1
        if self.uncovered_policy == POLICY_ERROR:
            raise UncoveredCharError(ch)
        if self.uncovered_policy == POLICY_FOLD:
            base = self._fold_base(ch)
            for cand in base:
                entry = self._letters.get(cand)
                if entry is not None:
                    # 折叠字符在同等价类内排在原字符之后（三级 +2）
                    return ((TAG_ALPHA, entry[0]), entry[1], 2,
                            "fold->%r" % cand)
                if cand in self._cjk:
                    pinyin, tone = self._cjk[cand]
                    return ((TAG_ALPHA, pinyin), tone, 2, "fold->%r" % cand)
                if cand in self._symbols:
                    return ((TAG_SYMBOL, self._symbols[cand]), 0, 2,
                            "fold->%r" % cand)
            fallback = base[0] if base else ch
            return ((self._unknown_tag, ord(fallback)), 0, 0,
                    "fold->codepoint")
        # 默认: 回退到码位
        return ((self._unknown_tag, ord(ch)), 0, 0, "codepoint")

    def _entries(self, text):
        """把文本切分为键条目序列 [(primary, secondary, tertiary, source)]。"""
        entries = []
        i = 0
        n = len(text)
        while i < n:
            ch = text[i]
            if self.numeric and self._is_ascii_digit(ch):
                j = i + 1
                while j < n and self._is_ascii_digit(text[j]):
                    j += 1
                entries.append(((TAG_NUMBER, int(text[i:j])), 0, 0,
                                "numeric:%s" % text[i:j]))
                i = j
                continue
            entry = self._letters.get(ch)
            if entry is not None:
                entries.append(((TAG_ALPHA, entry[0]), entry[1],
                                self._case_rank(ch), "letters"))
            elif ch in self._cjk:
                pinyin, tone = self._cjk[ch]
                entries.append(((TAG_ALPHA, pinyin), tone, 0, "cjk"))
            elif ch in self._symbols:
                entries.append(((TAG_SYMBOL, self._symbols[ch]), 0, 0,
                                "symbols"))
            else:
                entries.append(self._uncovered_entry(ch))
            i += 1
        return entries

    def key(self, text):
        """返回三级排序键 (primaries, secondaries, tertiaries)，可互相比较。"""
        entries = self._entries(text)
        return (tuple(e[0] for e in entries),
                tuple(e[1] for e in entries),
                tuple(e[2] for e in entries))

    def explain(self, text):
        """逐字符解释排序键来源，返回字典列表（顺序与文本一致）。"""
        out = []
        for e in self._entries(text):
            out.append({
                "source": e[3],
                "primary": e[0],
                "secondary": e[1],
                "tertiary": e[2],
            })
        return out

    # ------------------------------------------------------------- compare

    def compare(self, left, right):
        """全序比较器: 负数 / 0 / 正数。"""
        ka = self.key(left)
        kb = self.key(right)
        return (ka > kb) - (ka < kb)

    def sort(self, items):
        """稳定排序（list.sort 本身稳定），返回新列表。"""
        return sorted(items, key=self.key)

    # ------------------------------------------------------------- report

    def reset_uncovered(self):
        self._uncovered = {}

    def uncovered_report(self, items=None):
        """未覆盖字符报告: {字符: 次数}，按次数降序。

        若传入 items，则先扫描这些条目（不修改已有计数之外的副作用）。
        """
        if items is not None:
            for text in items:
                self.key(text)
        return dict(
            sorted(self._uncovered.items(), key=lambda kv: (-kv[1], kv[0]))
        )

    # ------------------------------------------------- completeness check

    def covered_chars(self):
        """配置声明覆盖的字符集合（含 numeric 开关覆盖的 ASCII 数字）。"""
        covered = set(self._letters) | set(self._symbols) | set(self._cjk)
        if self.numeric:
            covered.update("0123456789")
        return covered

    def coverage_check(self, max_codepoint=0x10FFFF):
        """规则完整性检查：扫描映射表与 Unicode 字符类别。

        报告未被任何规则覆盖的字符类别（含类别内未覆盖字符数与样例），
        并给出内容摘要 digest；同一配置 + 同一 unicodedata 版本下结果
        完全确定，可复算验证。
        """
        covered = self.covered_chars()
        stats = {}  # category -> [covered, uncovered, samples]
        for cp in range(max_codepoint + 1):
            ch = chr(cp)
            cat = unicodedata.category(ch)
            if cat in ("Cn", "Cs"):  # 未分配 / 代理项不参与统计
                continue
            entry = stats.setdefault(cat, [0, 0, []])
            if ch in covered:
                entry[0] += 1
            else:
                entry[1] += 1
                if len(entry[2]) < 5:
                    entry[2].append(ch)

        categories = {}
        uncovered_categories = []
        for cat in sorted(stats):
            cov, uncov, samples = stats[cat]
            categories[cat] = {"covered": cov, "uncovered": uncov}
            if uncov:
                uncovered_categories.append({
                    "category": cat,
                    "name": CATEGORY_NAMES.get(cat, cat),
                    "uncovered": uncov,
                    "samples": samples,
                })
        uncovered_categories.sort(key=lambda e: (-e["uncovered"],
                                                 e["category"]))

        report = {
            "config": self.name,
            "unidata_version": unicodedata.unidata_version,
            "covered_chars": len(covered),
            "categories": categories,
            "uncovered_categories": uncovered_categories,
        }
        payload = json.dumps(report, ensure_ascii=False, sort_keys=True)
        report["digest"] = hashlib.sha256(
            payload.encode("utf-8")).hexdigest()
        return report

    def format_coverage_report(self, report=None):
        """把 coverage_check 的结果渲染为确定性的文本报告。"""
        if report is None:
            report = self.coverage_check()
        lines = [
            "# 规则完整性检查报告",
            "# 配置: %s" % report["config"],
            "# Unicode 数据版本: %s" % report["unidata_version"],
            "# 规则覆盖字符数: %d" % report["covered_chars"],
            "# 摘要 digest: %s" % report["digest"],
            "#",
            "# 未被任何规则覆盖的字符类别:",
        ]
        for entry in report["uncovered_categories"]:
            samples = " ".join("U+%04X %s" % (ord(c), c)
                               for c in entry["samples"])
            lines.append("%s\t%s\t未覆盖 %d 字符\t样例: %s"
                         % (entry["category"], entry["name"],
                            entry["uncovered"], samples))
        return "\n".join(lines) + "\n"
