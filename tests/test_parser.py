import unittest

from tplcheck import TemplateSyntaxError, parse
from tplcheck.parser import Each, If, Placeholder, Text


class TestParser(unittest.TestCase):
    def test_empty_template(self):
        self.assertEqual(parse(""), [])

    def test_plain_text(self):
        nodes = parse("hello world")
        self.assertEqual(nodes, [Text("hello world", nodes[0].pos)])

    def test_placeholder_with_type_and_optional(self):
        (node,) = parse("{name:str?}")
        self.assertIsInstance(node, Placeholder)
        self.assertEqual((node.name, node.type, node.optional), ("name", "str", True))

    def test_placeholder_defaults(self):
        (node,) = parse("{name}")
        self.assertEqual((node.name, node.type, node.optional), ("name", "any", False))

    def test_escaped_braces(self):
        nodes = parse("{{name}}")
        self.assertEqual(len(nodes), 3)
        self.assertEqual("".join(n.value for n in nodes), "{name}")

    def test_lone_brace_is_error(self):
        with self.assertRaises(TemplateSyntaxError):
            parse("a { b")
        with self.assertRaises(TemplateSyntaxError):
            parse("a } b")

    def test_if_section(self):
        (node,) = parse("{#if ok}yes{/if}")
        self.assertIsInstance(node, If)
        self.assertEqual(node.cond, "ok")
        self.assertEqual(len(node.body), 1)

    def test_each_section(self):
        (node,) = parse("{#each items as it}{it}{/each}")
        self.assertIsInstance(node, Each)
        self.assertEqual((node.source, node.var), ("items", "it"))

    def test_deep_nesting(self):
        src = "{#if a}{#each xs as x}{#if b}{x}{/if}{/each}{/if}"
        (node,) = parse(src)
        self.assertIsInstance(node, If)
        self.assertIsInstance(node.body[0], Each)
        self.assertIsInstance(node.body[0].body[0], If)

    def test_unclosed_section(self):
        with self.assertRaises(TemplateSyntaxError):
            parse("{#if a}oops")

    def test_mismatched_close(self):
        with self.assertRaises(TemplateSyntaxError):
            parse("{#if a}{/each}")

    def test_stray_close(self):
        with self.assertRaises(TemplateSyntaxError):
            parse("{/if}")

    def test_unknown_type(self):
        with self.assertRaises(TemplateSyntaxError):
            parse("{x:uuid}")

    def test_position_tracking(self):
        node = parse("ab\n\n  {x}")[-1]
        self.assertEqual((node.pos.line, node.pos.column), (3, 3))


if __name__ == "__main__":
    unittest.main()
