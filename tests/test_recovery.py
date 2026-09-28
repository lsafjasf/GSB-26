"""错误恢复功能自测。运行：python3 -m unittest discover -s tests -v"""
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from miniparser import ParseError, parse, parse_strict, walk


def kinds(node):
    return [n.kind for n in walk(node)]


class TestSingleError(unittest.TestCase):
    SRC = "let x = 1;\nlet y = ;\nlet z = 3;\n"

    def setUp(self):
        self.result = parse(self.SRC)

    def test_partial_tree_returned(self):
        body = self.result.tree.props["body"]
        self.assertEqual([n.kind for n in body], ["let", "error", "let"])
        self.assertEqual(body[0].props["name"], "x")
        self.assertEqual(body[2].props["name"], "z")

    def test_error_list_complete(self):
        self.assertEqual(len(self.result.errors), 1)
        err = self.result.errors[0]
        self.assertEqual(err.expected, "表达式")
        self.assertEqual(err.actual, "';'")
        self.assertEqual((err.pos.line, err.pos.col), (2, 9))

    def test_uncertain_region_marked(self):
        body = self.result.tree.props["body"]
        self.assertFalse(body[0].recovered)
        self.assertTrue(body[1].recovered)
        self.assertFalse(body[2].recovered)
        regions = self.result.uncertain_regions()
        self.assertEqual(len(regions), 1)
        self.assertIs(regions[0], body[1])

    def test_incomplete_flag(self):
        self.assertTrue(self.result.incomplete)
        self.assertFalse(self.result.ok)


class TestMultipleIndependentErrors(unittest.TestCase):
    def test_all_errors_collected_and_sorted(self):
        src = "let a = ;\nlet b = 2;\nprint ;\nlet c = ;\n"
        result = parse(src)
        self.assertEqual(len(result.errors), 3)
        offsets = [e.pos.offset for e in result.errors]
        self.assertEqual(offsets, sorted(offsets))
        body = result.tree.props["body"]
        self.assertEqual([n.kind for n in body],
                         ["error", "let", "error", "error"])
        # 错误之间的正常语句不受影响
        self.assertEqual(body[1].props["name"], "b")
        self.assertFalse(body[1].recovered)

    def test_each_error_has_position_expected_actual(self):
        src = "let a = ;\nprint ;\n"
        result = parse(src)
        for err in result.errors:
            self.assertIsNotNone(err.pos)
            self.assertTrue(err.expected)
            self.assertTrue(err.actual)


class TestNestedErrors(unittest.TestCase):
    def test_error_inside_block_inside_if(self):
        src = (
            "if (x > 0) {\n"
            "  let a = ;\n"
            "  let b = 2;\n"
            "}\n"
            "let c = 3;\n"
        )
        result = parse(src)
        self.assertEqual(len(result.errors), 1)
        top = result.tree.props["body"]
        self.assertEqual([n.kind for n in top], ["if", "let"])
        if_node = top[0]
        block = if_node.props["then"]
        self.assertEqual(block.kind, "block")
        self.assertEqual([n.kind for n in block.props["body"]],
                         ["error", "let"])
        # 内层恢复不吞掉 '}'，外层结构完整保留
        self.assertEqual(top[1].props["name"], "c")
        # 恢复点之后的兄弟语句正常
        self.assertEqual(block.props["body"][1].props["name"], "b")
        self.assertFalse(block.props["body"][1].recovered)

    def test_error_in_then_and_else_branches(self):
        src = "if (x) { let a = ; } else { let b = ; }\nlet c = 1;\n"
        result = parse(src)
        self.assertEqual(len(result.errors), 2)
        if_node = result.tree.props["body"][0]
        self.assertEqual(if_node.props["then"].props["body"][0].kind, "error")
        self.assertEqual(if_node.props["else"].props["body"][0].kind, "error")
        self.assertEqual(result.tree.props["body"][1].props["name"], "c")

    def test_deeply_nested_error(self):
        src = "while (i > 0) { if (i == 2) { let v = ; } let w = 1; }\nlet ok = 2;\n"
        result = parse(src)
        self.assertEqual(len(result.errors), 1)
        top = result.tree.props["body"]
        self.assertEqual([n.kind for n in top], ["while", "let"])
        inner_if = top[0].props["body"].props["body"][0]
        self.assertEqual(inner_if.kind, "if")
        self.assertEqual(inner_if.props["then"].props["body"][0].kind, "error")
        # 内层错误之后的语句与顶层语句均完好
        self.assertEqual(top[0].props["body"].props["body"][1].props["name"], "w")
        self.assertEqual(top[1].props["name"], "ok")
        self.assertFalse(top[1].recovered)


class TestImmediateRecovery(unittest.TestCase):
    def test_missing_semicolon_phrase_level(self):
        # 缺 ';' 时补一个虚拟分号：不跳过任何 token，语句完整保留
        src = "let x = 1\nlet y = 2;\n"
        result = parse(src)
        self.assertEqual(len(result.errors), 1)
        err = result.errors[0]
        self.assertEqual(err.expected, "';'")
        self.assertEqual(err.actual, "'let'")
        body = result.tree.props["body"]
        self.assertEqual([n.kind for n in body], ["let", "let"])
        self.assertEqual(body[0].props["name"], "x")
        self.assertEqual(body[1].props["name"], "y")
        # 发生补齐的语句被标记为不确定，下一条不受影响
        self.assertTrue(body[0].recovered)
        self.assertFalse(body[1].recovered)

    def test_missing_semicolon_before_rbrace(self):
        src = "{ let a = 1 }\n"
        result = parse(src)
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(result.errors[0].expected, "';'")
        block = result.tree.props["body"][0]
        self.assertEqual(block.kind, "block")
        self.assertEqual(block.props["body"][0].props["name"], "a")

    def test_missing_semicolon_at_eof(self):
        result = parse("let x = 1")
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(result.errors[0].actual, "文件结尾")
        body = result.tree.props["body"]
        self.assertEqual(body[0].props["name"], "x")
        self.assertTrue(body[0].recovered)


class TestNoFalseSuccess(unittest.TestCase):
    def test_comma_reports_single_error(self):
        # 逗号是合法分隔符 token：同一处毛病只报一条错误，分类明确为 parse
        result = parse("let x = 1, 2;\n")
        at_comma = [e for e in result.errors
                    if (e.pos.line, e.pos.col) == (1, 10)]
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(len(at_comma), 1)
        err = at_comma[0]
        self.assertEqual(err.phase, "parse")
        self.assertEqual(err.expected, "';'")
        self.assertEqual(err.actual, "','")
        self.assertTrue(result.incomplete)
        self.assertFalse(result.ok)

    def test_unclosed_block_is_incomplete(self):
        result = parse("if (x) {\n  let a = 1;\n")
        self.assertTrue(result.incomplete)
        self.assertFalse(result.ok)
        self.assertTrue(any(e.expected == "'}'" for e in result.errors))

    def test_stray_token_is_incomplete(self):
        result = parse("let x = 1; }\n")
        self.assertTrue(result.incomplete)
        self.assertEqual(len(result.errors), 1)

    def test_lexer_error_is_incomplete(self):
        result = parse("let x = 1 @ 2;\n")
        self.assertTrue(result.incomplete)
        self.assertTrue(any(e.phase == "lex" for e in result.errors))
        # 坏字符被跳过后解析继续，不丢语句
        self.assertEqual(result.tree.props["body"][0].props["name"], "x")

    def test_clean_parse_not_incomplete(self):
        result = parse("let x = 1;\n")
        self.assertFalse(result.incomplete)
        self.assertTrue(result.ok)
        self.assertEqual(result.uncertain_regions(), [])


class TestDeterminism(unittest.TestCase):
    def test_repeated_parse_identical(self):
        src = "let a = ;\nif (x) { let b = 2 }\nlet c = ;\n"
        first = parse(src)
        for _ in range(5):
            again = parse(src)
            self.assertEqual(again, first)
            self.assertEqual(again.errors, first.errors)


class TestDifferentialAgainstStrict(unittest.TestCase):
    """对拍：正确输入上，容错模式结果必须与严格模式完全一致。"""

    VALID_PROGRAMS = [
        "let x = 1;\n",
        "let x = 1 + 2 * 3;\nprint x;\n",
        "let a = 1;\nlet b = 2;\nif (a < b) { print a; } else { print b; }\n",
        "while (x != 0) { let y = x - 1; print y; }\n",
        "let s = \"hi\";\nprint -s;\n",
        "if (x >= 1) if (x <= 10) print x;\n",
        "{ let a = 1; { let b = 2; print a + b; } }\n",
        "print (1 + 2) * (3 - 4) / 5;\n",
        "let flag = !x == 1;\n",
        "1 + 2;\n",
    ]

    def test_valid_programs_match_strict(self):
        for src in self.VALID_PROGRAMS:
            with self.subTest(src=src):
                result = parse(src)
                self.assertTrue(result.ok, msg=f"误报错误: {result.errors}")
                self.assertFalse(result.incomplete)
                self.assertEqual(result.errors, [])
                self.assertEqual(result.tree, parse_strict(src))
                self.assertEqual(result.uncertain_regions(), [])

    def test_generated_valid_programs_match_strict(self):
        rng = random.Random(20260927)

        def expr(depth):
            if depth == 0:
                return rng.choice(["1", "2", "x", "y", '"s"', "(1 + 2)"])
            choice = rng.random()
            if choice < 0.6:
                op = rng.choice(["+", "-", "*", "/", "==", "!=", "<", ">="])
                return f"{expr(depth - 1)} {op} {expr(depth - 1)}"
            if choice < 0.8:
                return f"-{expr(depth - 1)}"
            return f"({expr(depth - 1)})"

        def stmt(depth):
            choice = rng.random()
            if choice < 0.3:
                return f"let v{rng.randrange(100)} = {expr(2)};"
            if choice < 0.55:
                return f"print {expr(2)};"
            if choice < 0.75 and depth > 0:
                inner = " ".join(stmt(depth - 1) for _ in range(rng.randrange(1, 3)))
                tail = f" else {{ {stmt(depth - 1)} }}" if rng.random() < 0.5 else ""
                return f"if ({expr(1)}) {{ {inner} }}{tail}"
            if choice < 0.9 and depth > 0:
                return f"while ({expr(1)}) {{ {stmt(depth - 1)} }}"
            inner = " ".join(stmt(0) for _ in range(rng.randrange(1, 3)))
            return f"{{ {inner} }}"

        for _ in range(200):
            src = "\n".join(stmt(2) for _ in range(rng.randrange(1, 5))) + "\n"
            with self.subTest(src=src):
                result = parse(src)
                self.assertTrue(result.ok, msg=f"误报错误: {result.errors}")
                self.assertEqual(result.tree, parse_strict(src))

    def test_strict_mode_still_fails_fast(self):
        with self.assertRaises(ParseError):
            parse_strict("let x = ;\nlet y = 2;\n")


if __name__ == "__main__":
    unittest.main()
