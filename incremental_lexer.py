"""增量语法高亮分词器（仅标准库）。

语言模型：类 C 语言
  - 行注释   // ...
  - 块注释   /* ... */        （可跨行）
  - 普通字符串 "..."          （不可跨行，支持 \\ 转义）
  - 多行字符串 `...`          （可跨行，支持 \\ 转义）
  - 数字、标识符、关键字、标点

增量模型：
  每行独立保存 (tokens, end_state)。一行的分词结果只依赖于
  「行首状态」和「行文本」，因此编辑后只需从首个受影响行开始
  重新分词；当某行算出的行末状态与旧值一致时，其后续所有行的
  行首状态不变、文本不变，结果必然不变，传播立即停止。
"""

from dataclasses import dataclass

NORMAL, BLOCK_COMMENT, ML_STRING = 0, 1, 2

KEYWORDS = frozenset({
    "int", "char", "float", "double", "void", "if", "else", "while",
    "for", "return", "break", "continue", "struct", "const", "static",
})


@dataclass(frozen=True)
class Token:
    type: str        # COMMENT / STRING / NUMBER / KEYWORD / IDENT / PUNCT
    line: int        # 0 起始行号
    col: int         # 起始列（含）
    end_col: int     # 结束列（不含）


def tokenize_line(text, state):
    """对单行分词，返回 ([(type, col, end_col), ...], end_state)。"""
    tokens = []
    i, n = 0, len(text)
    while i < n:
        if state == BLOCK_COMMENT:
            j = text.find("*/", i)
            if j == -1:
                tokens.append(("COMMENT", i, n))
                i = n
            else:
                tokens.append(("COMMENT", i, j + 2))
                i = j + 2
                state = NORMAL
            continue
        if state == ML_STRING:
            start = i
            closed = False
            while i < n:
                ch = text[i]
                if ch == "\\":
                    i += 2
                    continue
                if ch == "`":
                    closed = True
                    break
                i += 1
            if closed:
                tokens.append(("STRING", start, i + 1))
                i += 1
                state = NORMAL
            else:
                tokens.append(("STRING", start, n))
                i = n
            continue
        # state == NORMAL
        ch = text[i]
        if ch in " \t\r":
            i += 1
            continue
        if text.startswith("//", i):
            tokens.append(("COMMENT", i, n))
            i = n
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            if j == -1:
                tokens.append(("COMMENT", i, n))
                i = n
                state = BLOCK_COMMENT
            else:
                tokens.append(("COMMENT", i, j + 2))
                i = j + 2
            continue
        if ch == '"':
            start = i
            i += 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == '"':
                    i += 1
                    break
                i += 1
            tokens.append(("STRING", start, min(i, n)))
            continue
        if ch == "`":
            start = i
            i += 1
            closed = False
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == "`":
                    i += 1
                    closed = True
                    break
                i += 1
            tokens.append(("STRING", start, min(i, n)))
            if not closed:
                state = ML_STRING
            continue
        if ch.isdigit():
            start = i
            while i < n and (text[i].isdigit() or text[i] == "."):
                i += 1
            tokens.append(("NUMBER", start, i))
            continue
        if ch.isalpha() or ch == "_":
            start = i
            while i < n and (text[i].isalnum() or text[i] == "_"):
                i += 1
            word = text[start:i]
            tokens.append(("KEYWORD" if word in KEYWORDS else "IDENT", start, i))
            continue
        tokens.append(("PUNCT", i, i + 1))
        i += 1
    return tokens, state


def full_tokenize(text):
    """全量分词（基准/对拍用）。"""
    out = []
    state = NORMAL
    for line_no, line in enumerate(text.split("\n")):
        toks, state = tokenize_line(line, state)
        out.extend(Token(t, line_no, a, b) for t, a, b in toks)
    return out


class IncrementalLexer:
    """支持单字符插入/删除、整段替换、撤销重做的增量分词器。"""

    def __init__(self, text=""):
        self.lines = text.split("\n")
        # 每行 (tokens, end_state)；None 表示待重算
        self._lexed = [None] * len(self.lines)
        self.last_relex_lines = 0   # 最近一次编辑实际重分词的行数
        self._undo = []
        self._redo = []
        self._relex_from(0, 0)

    # ---------- 增量核心 ----------

    def _relex_from(self, start, force_until):
        """重分词 [start, ...]；[start, force_until) 为编辑区，必须重算。

        编辑区保留的旧 end_state 作为停止提示：一旦某行（不早于编辑区
        末行）算出的行末状态与旧值一致，后续行的行首状态与文本均未变，
        分词结果必然不变，传播立即停止。
        """
        state = self._lexed[start - 1][1] if start > 0 else NORMAL
        i = start
        count = 0
        while i < len(self.lines):
            toks, end_state = tokenize_line(self.lines[i], state)
            old = self._lexed[i]
            self._lexed[i] = (toks, end_state)
            count += 1
            if i >= force_until - 1 and old is not None and old[1] == end_state:
                break
            state = end_state
            i += 1
        self.last_relex_lines = count

    def _splice(self, start, end, text):
        (sl, sc), (el, ec) = start, end
        new_lines = text.split("\n")
        prefix = self.lines[sl][:sc]
        suffix = self.lines[el][ec:]
        if len(new_lines) == 1:
            replacement = [prefix + new_lines[0] + suffix]
        else:
            replacement = ([prefix + new_lines[0]] + new_lines[1:-1]
                           + [new_lines[-1] + suffix])
        # 停止提示：仅编辑区最后一行保留旧 (tokens, end_state)。
        # 该行旧的 end_state 正是下游行的旧行首状态；若重算后相等，
        # 则下游所有行的行首状态与文本均未变，可安全停止传播。
        old_end_hint = self._lexed[el]
        self.lines[sl:el + 1] = replacement
        self._lexed[sl:el + 1] = [None] * (len(replacement) - 1) + [old_end_hint]
        self._relex_from(sl, sl + len(replacement))

    # ---------- 编辑操作 ----------

    @staticmethod
    def _advance(pos, text):
        line, col = pos
        parts = text.split("\n")
        if len(parts) == 1:
            return (line, col + len(text))
        return (line + len(parts) - 1, len(parts[-1]))

    def _get_text(self, start, end):
        (sl, sc), (el, ec) = start, end
        if sl == el:
            return self.lines[sl][sc:ec]
        parts = [self.lines[sl][sc:]]
        parts.extend(self.lines[sl + 1:el])
        parts.append(self.lines[el][:ec])
        return "\n".join(parts)

    def _edit(self, start, end, new_text, record=True):
        old_text = self._get_text(start, end)
        self._splice(start, end, new_text)
        if record:
            self._undo.append((start, end, old_text, new_text))
            self._redo.clear()

    def insert(self, line, col, text):
        """在 (line, col) 处插入文本（单字符插入即 len(text)==1）。"""
        self._edit((line, col), (line, col), text)

    def delete(self, start, end):
        """删除 [start, end) 区间（单字符删除即相邻两位置）。"""
        self._edit(start, end, "")

    def replace(self, start, end, text):
        """整段替换。"""
        self._edit(start, end, text)

    def undo(self):
        if not self._undo:
            return False
        start, end, old_text, new_text = self._undo.pop()
        new_end = self._advance(start, new_text)
        self._splice(start, new_end, old_text)
        self._redo.append((start, end, old_text, new_text))
        return True

    def redo(self):
        if not self._redo:
            return False
        start, end, old_text, new_text = self._redo.pop()
        self._splice(start, end, new_text)
        self._undo.append((start, end, old_text, new_text))
        return True

    # ---------- 查询 ----------

    def text(self):
        return "\n".join(self.lines)

    def tokens(self):
        out = []
        for line_no, entry in enumerate(self._lexed):
            toks, _ = entry
            out.extend(Token(t, line_no, a, b) for t, a, b in toks)
        return out
