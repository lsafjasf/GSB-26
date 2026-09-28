"""Feature iteration demo (escape decoding + comments + unterminated spans).

Run: python3 error_recovery_demo.py

Shows, with checkable positions:
1. raw text vs decoded text of STRING tokens (quote/backslash/newline escapes);
2. invalid escape errors with start..end spans, recovery keeps scanning;
3. line comments and nested / unterminated block comments;
4. unterminated string/comment errors carrying BOTH endpoints (half-open);
5. the same input re-run in strict mode -> LexicalError on the first fault.
"""

import copy
import json

from lexer_lib.lexer import Lexer, LexicalError

CONFIG = json.load(open("rules.json", encoding="utf-8"))

SAMPLE = r'''greet = "a\nb\tc\\d\"ok"
bad   = "x\q@"
done  = 'it\'s \r\n'
// line comment: " /* stays text
/* outer /* inner */ still open
tail = 1
'''

UNTERMINATED = 'x = "abc\ny = 2\nz = /* never closed\nend'


def show_tokens(tokens):
    print("  type     raw -> decoded            start      end(excl)")
    for t in tokens:
        print("  %-7s %-22s %-14s %d:%-3d -> %d:%d" % (
            t.type, repr(t.text), repr(t.decoded),
            t.start_line, t.start_col, t.end_line, t.end_col))


def show_errors(errors):
    for e in errors:
        (sl, sc), (el, ec) = e.span
        print("  %-20s %d:%d..%d:%d  raw=%r" % (
            e.message, sl, sc, el, ec, e.text))


def main():
    lexer = Lexer(CONFIG)

    print("=== 1) recovery mode: escapes, comments, errors ===")
    print("source (line-numbered):")
    for i, line in enumerate(SAMPLE.splitlines(), 1):
        print("  %d | %s" % (i, line))
    tokens, errors = lexer.tokenize(SAMPLE)
    print("-- tokens (raw text + decoded text) --")
    show_tokens(tokens)
    print("-- errors (%d), each with a start..end span --" % len(errors))
    show_errors(errors)

    print()
    print("=== 2) unterminated string & comment with both endpoints ===")
    for i, line in enumerate(UNTERMINATED.splitlines(), 1):
        print("  %d | %s" % (i, line))
    tokens, errors = lexer.tokenize(UNTERMINATED)
    print("-- tokens --")
    show_tokens(tokens)
    print("-- errors (half-open [start, end)) --")
    show_errors(errors)

    print()
    print("=== 3) strict mode raises on the first lexical error ===")
    strict_cfg = copy.deepcopy(CONFIG)
    strict_cfg["mode"] = "strict"
    strict = Lexer(strict_cfg)
    for src, label in [(r'"x\q"', "invalid escape"),
                       ('"abc', "unterminated string"),
                       ("a @ b", "illegal character"),
                       ("/* open", "unterminated comment")]:
        try:
            strict.tokenize(src)
        except LexicalError as exc:
            (sl, sc), (el, ec) = exc.err.span
            print("  %-20s %r -> LexicalError: %s at %d:%d..%d:%d" % (
                label, src, exc.err.message, sl, sc, el, ec))


if __name__ == "__main__":
    main()
