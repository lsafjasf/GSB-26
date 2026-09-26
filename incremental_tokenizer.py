"""Incremental syntax-highlighting tokenizer (Python 3, stdlib only).

Language "Mini":
  - line comments   // ...
  - block comments  /* ... */        (non-nested, may span lines)
  - strings         "..."            (single line, \\ escapes)
  - triple strings  \"\"\" ... \"\"\"    (may span lines)
  - numbers, identifiers, keywords, operators, error chars

Incremental model (same idea as most editors):
  * The document is stored as a list of lines.
  * For every line we cache:
      - state_in[i]   : lexer state at the START of the line
                        (NORMAL / BLOCK_COMMENT / TRIPLE_STRING)
      - tokens[i]     : tokens that START on this line; a multi-line token
                        stores its end as a line OFFSET relative to its
                        start line, so shifting lines never invalidates it.
      - open_off[i]   : if the line is INSIDE a multi-line construct, the
                        relative offset back to the line where that
                        construct's token starts, else None.
  * After an edit we re-tokenize from the start of the enclosing construct
    (walking back via open_off) and stop at the first line beyond the edit
    whose recomputed entry state equals the cached one: everything after
    that line is byte-identical to the previous tokenization.

Public token format: (type, start_line, start_col, end_line, end_col),
end exclusive, 0-based.
"""

NORMAL = 0
BLOCK_COMMENT = 1
TRIPLE_STRING = 2

KEYWORDS = frozenset({"if", "else", "while", "return", "def", "for", "in"})
OPERATORS = set("+-*/%=<>!&|^~()[]{}:;,.#@")
WHITESPACE = " \t\r"


def tokenize_line(line, state):
    """Tokenize one line given the entry state.

    Returns (tokens, state_out, closed_at):
      tokens    : list of [type, start_col, end_line_offset, end_col];
                  an unterminated multi-line token has end fields = None.
      state_out : lexer state at end of line.
      closed_at : if a carried multi-line token closes on this line, the
                  column just past its closing delimiter, else None.
    """
    tokens = []
    closed_at = None
    n = len(line)
    i = 0
    if state == BLOCK_COMMENT:
        j = line.find("*/")
        if j < 0:
            return tokens, BLOCK_COMMENT, None
        closed_at = j + 2
        i = j + 2
    elif state == TRIPLE_STRING:
        j = line.find('"""')
        if j < 0:
            return tokens, TRIPLE_STRING, None
        closed_at = j + 3
        i = j + 3
    while i < n:
        c = line[i]
        if c in WHITESPACE:
            i += 1
        elif line.startswith("//", i):
            tokens.append(["COMMENT", i, 0, n])
            i = n
        elif line.startswith("/*", i):
            j = line.find("*/", i + 2)
            if j < 0:
                tokens.append(["COMMENT", i, None, None])
                return tokens, BLOCK_COMMENT, closed_at
            tokens.append(["COMMENT", i, 0, j + 2])
            i = j + 2
        elif line.startswith('"""', i):
            j = line.find('"""', i + 3)
            if j < 0:
                tokens.append(["STRING", i, None, None])
                return tokens, TRIPLE_STRING, closed_at
            tokens.append(["STRING", i, 0, j + 3])
            i = j + 3
        elif c == '"':
            j = i + 1
            while j < n:
                if line[j] == "\\":
                    j += 2
                    continue
                if line[j] == '"':
                    break
                j += 1
            end = j + 1 if j < n else n
            tokens.append(["STRING", i, 0, end])
            i = end
        elif c.isdigit():
            j = i + 1
            while j < n and (line[j].isdigit() or line[j] == "."):
                j += 1
            tokens.append(["NUMBER", i, 0, j])
            i = j
        elif c.isalpha() or c == "_":
            j = i + 1
            while j < n and (line[j].isalnum() or line[j] == "_"):
                j += 1
            word = line[i:j]
            tokens.append(["KEYWORD" if word in KEYWORDS else "IDENT", i, 0, j])
            i = j
        elif c in OPERATORS:
            tokens.append(["OP", i, 0, i + 1])
            i += 1
        else:
            tokens.append(["ERROR", i, 0, i + 1])
            i += 1
    return tokens, NORMAL, closed_at


def _advance(pos, text):
    """Position obtained by inserting `text` at `pos`."""
    line, col = pos
    parts = text.split("\n")
    if len(parts) == 1:
        return (line, col + len(parts[0]))
    return (line + len(parts) - 1, len(parts[-1]))


class Document:
    """Editable document with incremental tokenization and undo/redo."""

    def __init__(self, text=""):
        self._lines = text.split("\n")
        n = len(self._lines)
        self._state_in = [NORMAL] * n
        self._tokens = [[] for _ in range(n)]
        self._open_off = [None] * n
        self.last_retokenized_lines = 0
        self._undo = []
        self._redo = []
        self._retokenize_from(0, n)

    # ---------------------------------------------------------- queries
    @property
    def text(self):
        return "\n".join(self._lines)

    @property
    def line_count(self):
        return len(self._lines)

    def tokens(self):
        """Flat token list: (type, start_line, start_col, end_line, end_col)."""
        out = []
        for i, toks in enumerate(self._tokens):
            for t in toks:
                out.append((t[0], i, t[1], i + t[2], t[3]))
        return out

    def line_states(self):
        """Entry lexer state of every line (for verification/debug)."""
        return list(self._state_in)

    # ---------------------------------------------------------- edits
    def insert(self, pos, text):
        self.replace(pos, pos, text)

    def delete(self, start, end):
        self.replace(start, end, "")

    def replace(self, start, end, text):
        removed = self._text_between(start, end)
        self._apply_replace(start, end, text)
        self._undo.append((start, end, removed, text))
        self._redo.clear()

    def undo(self):
        if not self._undo:
            return False
        start, end, removed, inserted = self._undo.pop()
        self._apply_replace(start, _advance(start, inserted), removed)
        self._redo.append((start, end, removed, inserted))
        return True

    def redo(self):
        if not self._redo:
            return False
        start, end, removed, inserted = self._redo.pop()
        self._apply_replace(start, end, inserted)
        self._undo.append((start, end, removed, inserted))
        return True

    # ------------------------------------------------------- internals
    def _text_between(self, start, end):
        sl, sc = start
        el, ec = end
        if sl == el:
            return self._lines[sl][sc:ec]
        parts = [self._lines[sl][sc:]]
        parts.extend(self._lines[sl + 1:el])
        parts.append(self._lines[el][:ec])
        return "\n".join(parts)

    def _apply_replace(self, start, end, text):
        sl, sc = start
        el, ec = end
        assert 0 <= sl <= el < len(self._lines), "bad range"
        # 1. walk back to the start of any multi-line construct that
        #    covers the first edited line (must happen before splicing).
        scan_start = sl
        while self._open_off[scan_start] is not None:
            scan_start -= self._open_off[scan_start]
        # 2. splice the text and the cache arrays (relative offsets in
        #    tokens/open_off make the shifted tail stay valid).
        prefix = self._lines[sl][:sc]
        suffix = self._lines[el][ec:]
        new_lines = (prefix + text + suffix).split("\n")
        count = len(new_lines)
        self._lines[sl:el + 1] = new_lines
        self._state_in[sl:el + 1] = [NORMAL] * count
        self._tokens[sl:el + 1] = [[] for _ in range(count)]
        self._open_off[sl:el + 1] = [None] * count
        # 3. re-tokenize only what can actually change.
        self._retokenize_from(scan_start, sl + count)

    def _retokenize_from(self, start, min_end):
        lines = self._lines
        state = self._state_in[start]
        opener = None      # line where the current open token started
        open_tok = None    # the open token object (its end gets patched)
        i = start
        while True:
            self._state_in[i] = state
            self._open_off[i] = (i - opener) if state != NORMAL else None
            toks, state_out, closed_at = tokenize_line(lines[i], state)
            if closed_at is not None:
                open_tok[2] = i - opener
                open_tok[3] = closed_at
                open_tok = None
                opener = None
            self._tokens[i] = toks
            if state_out != NORMAL and open_tok is None:
                open_tok = toks[-1]
                opener = i
            state = state_out
            i += 1
            if i >= min_end:
                if i >= len(lines):
                    break
                # stop only in NORMAL state with a matching cached state:
                # then the tail is provably identical to what is cached.
                if state == NORMAL and self._state_in[i] == NORMAL:
                    break
        if open_tok is not None:  # unterminated construct runs to EOF
            open_tok[2] = (len(lines) - 1) - opener
            open_tok[3] = len(lines[-1])
        self.last_retokenized_lines = i - start


def full_tokenize(text):
    """From-scratch tokenization used as the reference for differential
    testing: builds a fresh Document and returns its token list."""
    return Document(text).tokens()
