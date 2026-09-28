"""Unit tests for the configurable lexer. Run: python3 -m unittest -v"""

import copy
import json
import unittest

from lexer_lib.lexer import Lexer, LexicalError

CONFIG = json.load(open("rules.json", encoding="utf-8"))
STRICT = copy.deepcopy(CONFIG)
STRICT["mode"] = "strict"


def lex(src, strict=False):
    return Lexer(STRICT if strict else CONFIG).tokenize(src)


class TestBasics(unittest.TestCase):
    def test_empty_input(self):
        tokens, errors = lex("")
        self.assertEqual(tokens, [])
        self.assertEqual(errors, [])

    def test_whitespace_only(self):
        tokens, errors = lex("   \t\n\r\n  ")
        self.assertEqual(tokens, [])
        self.assertEqual(errors, [])

    def test_simple_statement(self):
        tokens, errors = lex("int x = 42;")
        self.assertEqual(errors, [])
        self.assertEqual([t.type for t in tokens],
                         ["KEYWORD", "IDENT", "OP", "INT", "OP"])
        self.assertEqual([t.text for t in tokens], ["int", "x", "=", "42", ";"])

    def test_positions(self):
        tokens, _ = lex("ab\ncd =\n  ef")
        self.assertEqual(tokens[0].pos(), ("IDENT", "ab", 1, 1, 1, 3))
        self.assertEqual(tokens[1].pos(), ("IDENT", "cd", 2, 1, 2, 3))
        self.assertEqual(tokens[2].pos(), ("OP", "=", 2, 4, 2, 5))
        self.assertEqual(tokens[3].pos(), ("IDENT", "ef", 3, 3, 3, 5))

    def test_longest_match_and_priority(self):
        tokens, _ = lex("a==b a=b a<=b")
        ops = [t.text for t in tokens if t.type == "OP"]
        self.assertEqual(ops, ["==", "=", "<="])
        tokens, _ = lex("1.5")
        self.assertEqual(tokens[0].type, "FLOAT")
        self.assertEqual(tokens[0].text, "1.5")

    def test_keyword_vs_ident(self):
        tokens, _ = lex("if iffy else_")
        self.assertEqual([t.type for t in tokens], ["KEYWORD", "IDENT", "IDENT"])


class TestEscapeDecoding(unittest.TestCase):
    def test_raw_and_decoded_fields(self):
        # every token carries both raw text and decoded text
        tokens, errors = lex(r'"a\nb\tc\\d\"e"')
        self.assertEqual(errors, [])
        t = tokens[0]
        self.assertEqual(t.type, "STRING")
        self.assertEqual(t.text, r'"a\nb\tc\\d\"e"')
        self.assertEqual(t.decoded, "a\nb\tc\\d\"e")
        # non-string tokens: decoded is identical to raw text
        tokens, _ = lex("int 42")
        self.assertTrue(all(t.text == t.decoded for t in tokens))

    def test_single_quote_and_simple_escapes(self):
        tokens, errors = lex(r"'it\'s \r\0'")
        self.assertEqual(errors, [])
        self.assertEqual(tokens[0].text, r"'it\'s \r\0'")
        self.assertEqual(tokens[0].decoded, "it's \r\0")

    def test_empty_string(self):
        tokens, errors = lex('"" x')
        self.assertEqual(errors, [])
        self.assertEqual((tokens[0].text, tokens[0].decoded), ('""', ""))

    def test_escaped_quote_does_not_close(self):
        tokens, errors = lex(r'"a\"b" x')
        self.assertEqual(errors, [])
        self.assertEqual(tokens[0].text, r'"a\"b"')
        self.assertEqual(tokens[0].decoded, 'a"b')
        self.assertEqual(tokens[1].text, "x")

    def test_line_continuation(self):
        # backslash + real newline is consumed; string spans both lines
        tokens, errors = lex('"a\\\nb" x')
        self.assertEqual(errors, [])
        self.assertEqual(len(tokens), 2)
        t = tokens[0]
        self.assertEqual(t.text, '"a\\\nb"')
        self.assertEqual((t.start_line, t.start_col), (1, 1))
        self.assertEqual((t.end_line, t.end_col), (2, 3))
        self.assertEqual(t.decoded, "a\nb")

    def test_invalid_escape_error_and_recovery(self):
        tokens, errors = lex(r'"a\qb\@c"')
        self.assertEqual(tokens[0].text, r'"a\qb\@c"')
        # recovery keeps the escaped characters verbatim in decoded text
        self.assertEqual(tokens[0].decoded, "aqb@c")
        self.assertEqual([e.message for e in errors],
                         ["invalid escape", "invalid escape"])
        # positions point at the backslash .. the offending character
        self.assertEqual((errors[0].line, errors[0].col), (1, 3))
        self.assertEqual((errors[0].end_line, errors[0].end_col), (1, 5))
        self.assertEqual(errors[0].text, r"\q")
        self.assertEqual((errors[1].line, errors[1].col), (1, 6))
        self.assertEqual((errors[1].end_line, errors[1].end_col), (1, 8))
        self.assertEqual(errors[1].text, r"\@")
        # tokenization still produced exactly one STRING token
        self.assertEqual(len(tokens), 1)

    def test_invalid_escape_position_after_newline(self):
        tokens, errors = lex('"x"\n"a\\qb"')
        esc = [e for e in errors if e.message == "invalid escape"]
        self.assertEqual(len(esc), 1)
        self.assertEqual((esc[0].line, esc[0].col), (2, 3))
        self.assertEqual((esc[0].end_line, esc[0].end_col), (2, 5))

    def test_trailing_backslash_at_eof(self):
        tokens, errors = lex('"abc\\')
        self.assertEqual(tokens[0].text, '"abc\\')
        messages = [e.message for e in errors]
        self.assertEqual(messages, ["invalid escape", "unterminated string"])
        bad, unterm = errors
        self.assertEqual((bad.line, bad.col), (1, 5))
        self.assertEqual((bad.end_line, bad.end_col), (1, 6))
        self.assertEqual(bad.text, "\\")
        self.assertEqual((unterm.line, unterm.col), (1, 1))
        self.assertEqual((unterm.end_line, unterm.end_col), (1, 6))

    def test_custom_escape_table(self):
        cfg = copy.deepcopy(CONFIG)
        cfg["strings"]["escapes"] = {"n": "\n", "\\": "\\"}
        tokens, errors = Lexer(cfg).tokenize(r'"\n\t"')
        # \t is not declared -> invalid escape, recovered verbatim
        self.assertEqual(tokens[0].decoded, "\nt")
        self.assertEqual([e.message for e in errors], ["invalid escape"])


class TestComments(unittest.TestCase):
    def test_line_comment(self):
        tokens, errors = lex("a // hello \" /* \nb")
        self.assertEqual(errors, [])
        self.assertEqual([t.text for t in tokens], ["a", "// hello \" /* ", "b"])
        self.assertEqual(tokens[1].type, "COMMENT")
        self.assertEqual(tokens[1].decoded, tokens[1].text)

    def test_line_comment_at_eof(self):
        tokens, errors = lex("x // no newline at end")
        self.assertEqual(errors, [])
        self.assertEqual(len(tokens), 2)
        self.assertEqual(tokens[1].end_col, 23)

    def test_nested_block_comment(self):
        tokens, errors = lex("/* a /* b /* c */ d */ e */ x")
        self.assertEqual(errors, [])
        self.assertEqual(tokens[0].type, "COMMENT")
        self.assertEqual(tokens[0].text, "/* a /* b /* c */ d */ e */")
        self.assertEqual(tokens[1].text, "x")

    def test_deep_nested_comment(self):
        depth = 5000
        src = "/*" * depth + "core" + "*/" * depth + " tail"
        tokens, errors = lex(src)
        self.assertEqual(errors, [])
        self.assertEqual(tokens[0].type, "COMMENT")
        self.assertEqual(tokens[1].text, "tail")

    def test_non_nested_block_config(self):
        cfg = copy.deepcopy(CONFIG)
        cfg["comments"]["block"][0]["nested"] = False
        tokens, errors = Lexer(cfg).tokenize("/* a /* b */ tail */ x")
        # first */ closes the comment; nesting is literal text
        self.assertEqual(errors, [])
        self.assertEqual(tokens[0].text, "/* a /* b */")
        self.assertEqual(tokens[1].text, "tail")


class TestUnterminatedSpans(unittest.TestCase):
    def test_unterminated_string_eof(self):
        tokens, errors = lex('x "abc')
        self.assertEqual(tokens[1].type, "STRING")
        self.assertEqual(tokens[1].text, '"abc')
        self.assertEqual(tokens[1].decoded, "abc")
        self.assertEqual(len(errors), 1)
        e = errors[0]
        self.assertEqual(e.message, "unterminated string")
        self.assertEqual((e.line, e.col), (1, 3))
        self.assertEqual((e.end_line, e.end_col), (1, 7))
        self.assertEqual((e.offset, e.end_offset), (2, 6))
        self.assertEqual(e.text, '"abc')

    def test_unterminated_string_newline_span(self):
        tokens, errors = lex('"abc\nnext')
        self.assertEqual(tokens[0].text, '"abc')
        e = errors[0]
        self.assertEqual(e.message, "unterminated string")
        # half-open end points at the newline (newline excluded from token)
        self.assertEqual(e.span, ((1, 1), (1, 5)))
        self.assertEqual(e.end_offset, 4)
        # recovery continues on the next line
        self.assertEqual(tokens[1].text, "next")
        self.assertEqual((tokens[1].start_line, tokens[1].start_col), (2, 1))

    def test_unterminated_string_with_escapes_span(self):
        # last \\ is a VALID escaped backslash; missing closing quote only
        tokens, errors = lex(r'"a\nb\\')
        self.assertEqual([e.message for e in errors], ["unterminated string"])
        self.assertEqual(tokens[0].decoded, "a\nb\\")
        self.assertEqual(errors[0].span, ((1, 1), (1, 8)))

    def test_unterminated_string_trailing_escape_span(self):
        tokens, errors = lex('"a\\nb\\')
        self.assertEqual([e.message for e in errors],
                         ["invalid escape", "unterminated string"])
        bad, unterm = errors
        self.assertEqual(bad.span, ((1, 6), (1, 7)))  # trailing backslash
        self.assertEqual(unterm.span, ((1, 1), (1, 7)))

    def test_unterminated_block_comment_span(self):
        tokens, errors = lex("/* a /* b */")
        self.assertEqual(tokens[0].type, "COMMENT")
        e = errors[0]
        self.assertEqual(e.message, "unterminated comment")
        self.assertEqual((e.line, e.col), (1, 1))
        self.assertEqual((e.end_line, e.end_col), (1, 13))
        self.assertEqual(e.text, "/* a /* b */")

    def test_unterminated_block_comment_multiline_span(self):
        src = "/* line1\nline2\nstill open"
        tokens, errors = lex(src)
        e = errors[0]
        self.assertEqual(e.message, "unterminated comment")
        self.assertEqual(e.span, ((1, 1), (3, 11)))
        self.assertEqual(e.text, src)

    def test_recovery_after_unterminated_continues(self):
        tokens, errors = lex('"oops\nint x = 1;')
        self.assertEqual(errors[0].message, "unterminated string")
        self.assertEqual([t.type for t in tokens],
                         ["STRING", "KEYWORD", "IDENT", "OP", "INT", "OP"])


class TestErrorRecoveryAndStrict(unittest.TestCase):
    def test_illegal_characters_skipped(self):
        tokens, errors = lex("a @ b # $\n`c")
        self.assertEqual([t.text for t in tokens], ["a", "b", "c"])
        self.assertEqual(len(errors), 4)
        self.assertTrue(all(e.message == "illegal character" for e in errors))
        self.assertEqual((errors[0].line, errors[0].col), (1, 3))
        self.assertEqual(errors[0].span, ((1, 3), (1, 4)))
        self.assertEqual((errors[3].line, errors[3].col), (2, 1))
        self.assertEqual(errors[3].span, ((2, 1), (2, 2)))

    def test_recovery_continues_after_error(self):
        tokens, errors = lex("@if x")
        self.assertEqual([t.type for t in tokens], ["KEYWORD", "IDENT"])
        self.assertEqual(len(errors), 1)

    def test_strict_mode_raises_first_error(self):
        with self.assertRaises(LexicalError) as ctx:
            lex("a @ b", strict=True)
        err = ctx.exception.err
        self.assertEqual(err.message, "illegal character")
        self.assertEqual(err.span, ((1, 3), (1, 4)))

    def test_strict_mode_invalid_escape(self):
        with self.assertRaises(LexicalError) as ctx:
            lex(r'"\q"', strict=True)
        err = ctx.exception.err
        self.assertEqual(err.message, "invalid escape")
        self.assertEqual(err.span, ((1, 2), (1, 4)))

    def test_strict_mode_unterminated_string(self):
        with self.assertRaises(LexicalError) as ctx:
            lex('"abc', strict=True)
        err = ctx.exception.err
        self.assertEqual(err.message, "unterminated string")
        self.assertEqual(err.span, ((1, 1), (1, 5)))

    def test_strict_mode_clean_input(self):
        tokens, errors = lex("int x = 42;", strict=True)
        self.assertEqual(errors, [])
        self.assertEqual(len(tokens), 5)


class TestEdgeCases(unittest.TestCase):
    def test_very_long_identifier(self):
        src = "a" * 200_000
        tokens, errors = lex(src)
        self.assertEqual(errors, [])
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0].text, src)
        self.assertEqual((tokens[0].end_line, tokens[0].end_col), (1, 200_001))

    def test_non_ascii_identifier(self):
        tokens, errors = lex("中文变量 = αβγ + café")
        self.assertEqual(errors, [])
        idents = [t.text for t in tokens if t.type == "IDENT"]
        self.assertEqual(idents, ["中文变量", "αβγ", "café"])

    def test_multiline_positions_after_comment(self):
        tokens, _ = lex("/* line1\nline2\nline3 */x")
        self.assertEqual(tokens[1].text, "x")
        self.assertEqual((tokens[1].start_line, tokens[1].start_col), (3, 9))


if __name__ == "__main__":
    unittest.main()
