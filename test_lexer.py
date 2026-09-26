"""Unit tests for the configurable lexer. Run: python3 -m unittest -v"""

import json
import unittest

from lexer_lib.lexer import Lexer

CONFIG = json.load(open("rules.json", encoding="utf-8"))


def lex(src):
    return Lexer(CONFIG).tokenize(src)


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


class TestStringsAndComments(unittest.TestCase):
    def test_escaped_quote(self):
        tokens, errors = lex(r'"a\"b" x')
        self.assertEqual(errors, [])
        self.assertEqual(tokens[0].text, r'"a\"b"')
        self.assertEqual(tokens[1].text, "x")

    def test_unterminated_string_eof(self):
        tokens, errors = lex('x "abc')
        self.assertEqual(tokens[1].type, "STRING")
        self.assertEqual(tokens[1].text, '"abc')
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].message, "unterminated string")
        self.assertEqual((errors[0].line, errors[0].col), (1, 3))

    def test_unterminated_string_newline(self):
        tokens, errors = lex('"abc\nnext')
        self.assertEqual(tokens[0].text, '"abc')
        self.assertEqual(errors[0].message, "unterminated string")
        self.assertEqual(tokens[1].text, "next")
        self.assertEqual((tokens[1].start_line, tokens[1].start_col), (2, 1))

    def test_line_comment(self):
        tokens, errors = lex("a // hello \" /* \nb")
        self.assertEqual(errors, [])
        self.assertEqual([t.text for t in tokens], ["a", "// hello \" /* ", "b"])
        self.assertEqual(tokens[1].type, "COMMENT")

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

    def test_unterminated_block_comment(self):
        tokens, errors = lex("/* a /* b */")
        self.assertEqual(tokens[0].type, "COMMENT")
        self.assertEqual(errors[0].message, "unterminated comment")
        self.assertEqual((errors[0].line, errors[0].col), (1, 1))


class TestErrorRecovery(unittest.TestCase):
    def test_illegal_characters_skipped(self):
        tokens, errors = lex("a @ b # $\n`c")
        self.assertEqual([t.text for t in tokens], ["a", "b", "c"])
        self.assertEqual(len(errors), 4)
        self.assertTrue(all(e.message == "illegal character" for e in errors))
        self.assertEqual((errors[0].line, errors[0].col), (1, 3))
        self.assertEqual((errors[3].line, errors[3].col), (2, 1))

    def test_recovery_continues_after_error(self):
        tokens, errors = lex("@if x")
        self.assertEqual([t.type for t in tokens], ["KEYWORD", "IDENT"])
        self.assertEqual(len(errors), 1)


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
