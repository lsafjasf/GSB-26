"""Test suite for richtext2text. Run: python3 -m unittest discover -s tests -v"""

import re
import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from richtext2text import Config, convert  # noqa: E402

SKIP_TAGS = {"script", "style", "head", "title", "noscript", "template"}


def visible_words(html: str):
    """Reference extractor: whitespace-separated tokens of all visible
    text (data outside script/style/head), used by the conservation
    invariant tests. Independent of the library's own parser."""

    class Extractor(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.parts = []
            self._skip = 0

        def handle_starttag(self, tag, attrs):
            if tag in SKIP_TAGS:
                self._skip += 1

        def handle_endtag(self, tag):
            if tag in SKIP_TAGS and self._skip:
                self._skip -= 1

        def handle_data(self, data):
            if not self._skip:
                self.parts.append(data)

    ex = Extractor()
    ex.feed(html)
    ex.close()
    text = re.sub(r"\s+", " ", " ".join(ex.parts))
    return [w for w in text.split(" ") if w]


def assert_conserved(testcase, html, output):
    """Invariant: every visible text token of the source must appear in
    the plain-text output."""
    for word in visible_words(html):
        testcase.assertIn(
            word, output,
            msg=f"visible token {word!r} missing from output")


class TestBasic(unittest.TestCase):
    def test_empty_document(self):
        self.assertEqual(convert(""), "")
        self.assertEqual(convert("   \n\t  "), "")
        self.assertEqual(convert("<html><head></head><body></body></html>"),
                         "")

    def test_plain_text(self):
        self.assertEqual(convert("hello world"), "hello world\n")

    def test_whitespace_collapsed(self):
        self.assertEqual(convert("<p>a   b\n\tc</p>"), "a b c\n")

    def test_entities(self):
        self.assertEqual(convert("<p>1 &lt; 2 &amp; 3 &gt; 2</p>"),
                         "1 < 2 & 3 > 2\n")

    def test_paragraphs_separated_by_blank_line(self):
        out = convert("<p>first</p><p>second</p>")
        self.assertEqual(out, "first\n\nsecond\n")

    def test_script_and_style_dropped(self):
        out = convert("<p>keep</p><script>var x=1;</script>"
                      "<style>.a{color:red}</style>")
        self.assertEqual(out, "keep\n")

    def test_br_line_break(self):
        self.assertEqual(convert("<p>line1<br>line2</p>"),
                         "line1\nline2\n")

    def test_headings(self):
        out = convert("<h1>Title</h1><h3>Sub</h3><p>body</p>")
        self.assertEqual(out, "# Title\n\n### Sub\n\nbody\n")

    def test_headings_plain_style(self):
        cfg = Config(heading_style="plain")
        self.assertEqual(convert("<h2>Head</h2>", cfg), "Head\n")


class TestLists(unittest.TestCase):
    def test_unordered_list(self):
        out = convert("<ul><li>a</li><li>b</li></ul>")
        self.assertEqual(out, "- a\n- b\n")

    def test_ordered_list_numbering(self):
        out = convert("<ol><li>a</li><li>b</li><li>c</li></ol>")
        self.assertEqual(out, "1. a\n2. b\n3. c\n")

    def test_ordered_list_start_attribute(self):
        out = convert('<ol start="5"><li>a</li><li>b</li></ol>')
        self.assertEqual(out, "5. a\n6. b\n")

    def test_nested_list_indentation(self):
        html = ("<ul><li>one<ul><li>one-a</li>"
                "<li>one-b<ol><li>deep</li></ol></li></ul></li>"
                "<li>two</li></ul>")
        out = convert(html)
        self.assertEqual(
            out,
            "- one\n"
            "  - one-a\n"
            "  - one-b\n"
            "    1. deep\n"
            "- two\n")

    def test_deeply_nested_list(self):
        depth = 100
        html = "leaf"
        for i in range(depth):
            html = f"<ul><li>level-{i}{html}</li></ul>" \
                if i % 2 == 0 else f"<ol><li>level-{i}{html}</li></ol>"
        out = convert(html)
        self.assertIn("leaf", out)
        for i in range(depth):
            self.assertIn(f"level-{i}", out)
        # deepest content must be indented (hierarchy preserved)
        leaf_line = next(l for l in out.splitlines() if "leaf" in l)
        self.assertTrue(leaf_line.startswith(" " * 10))

    def test_custom_marker(self):
        cfg = Config(unordered_marker="*")
        self.assertEqual(convert("<ul><li>x</li></ul>", cfg), "* x\n")


class TestBlocks(unittest.TestCase):
    def test_blockquote(self):
        out = convert("<blockquote><p>quoted text</p></blockquote>")
        self.assertEqual(out, "> quoted text\n")

    def test_nested_blockquote(self):
        out = convert(
            "<blockquote><p>outer</p>"
            "<blockquote><p>inner</p></blockquote></blockquote>")
        self.assertEqual(out, "> outer\n>\n> > inner\n")

    def test_code_block_verbatim(self):
        code = "def f(x):\n    return   x  *  2\n"
        html = "<pre><code>" + code.replace("<", "&lt;") + "</code></pre>"
        out = convert(html)
        # no whitespace collapsing, no paragraph merging
        self.assertEqual(out, "def f(x):\n    return   x  *  2\n")

    def test_code_block_with_markup_chars(self):
        html = "<pre>if (a &lt; b &amp;&amp; c &gt; d) {\n  go();\n}</pre>"
        out = convert(html)
        self.assertEqual(out,
                         "if (a < b && c > d) {\n  go();\n}\n")

    def test_code_block_surrounded_by_blank_lines(self):
        out = convert("<p>before</p><pre>x = 1</pre><p>after</p>")
        self.assertEqual(out, "before\n\nx = 1\n\nafter\n")

    def test_hr(self):
        self.assertEqual(convert("<p>a</p><hr><p>b</p>"), "a\n\n---\n\nb\n")


class TestTables(unittest.TestCase):
    def test_simple_table(self):
        html = ("<table><tr><th>Name</th><th>Age</th></tr>"
                "<tr><td>Ann</td><td>30</td></tr>"
                "<tr><td>Bob</td><td>25</td></tr></table>")
        out = convert(html)
        self.assertEqual(
            out,
            "Name | Age\n"
            "---- | ---\n"
            "Ann | 30\n"
            "Bob | 25\n")
        # structure: header divider under the th row
        lines = out.splitlines()
        self.assertTrue(set(lines[1]) <= set("-| "))

    def test_table_without_header(self):
        html = "<table><tr><td>1</td><td>2</td></tr></table>"
        self.assertEqual(convert(html), "1 | 2\n")

    def test_table_thead_tbody(self):
        html = ("<table><thead><tr><th>H</th></tr></thead>"
                "<tbody><tr><td>x</td></tr><tr><td>y</td></tr></tbody>"
                "</table>")
        out = convert(html)
        self.assertEqual(out, "H\n---\nx\ny\n")

    def test_wide_table(self):
        cols = 200
        header = "".join(f"<th>h{i}</th>" for i in range(cols))
        row = "".join(f"<td>c{i}</td>" for i in range(cols))
        html = f"<table><tr>{header}</tr><tr>{row}</tr></table>"
        out = convert(html)
        lines = out.splitlines()
        self.assertEqual(len(lines), 3)  # header, divider, row
        self.assertEqual(lines[0].count(" | "), cols - 1)
        for i in range(cols):
            self.assertIn(f"c{i}", lines[2])

    def test_custom_separator(self):
        cfg = Config(table_column_separator=" || ")
        out = convert("<table><tr><td>a</td><td>b</td></tr></table>", cfg)
        self.assertEqual(out, "a || b\n")


class TestLinks(unittest.TestCase):
    def test_link_url_mode_default(self):
        out = convert('<p>see <a href="https://ex.com">here</a></p>')
        self.assertEqual(out, "see here <https://ex.com>\n")

    def test_link_text_mode(self):
        cfg = Config(link_mode="text")
        out = convert('<p>see <a href="https://ex.com">here</a></p>', cfg)
        self.assertEqual(out, "see here\n")
        self.assertNotIn("http", out)

    def test_link_mode_consistent_across_document(self):
        html = ('<p><a href="https://a.com">A</a> and '
                '<a href="https://b.com">B</a></p>')
        out_url = convert(html, Config(link_mode="url"))
        out_text = convert(html, Config(link_mode="text"))
        self.assertIn("<https://a.com>", out_url)
        self.assertIn("<https://b.com>", out_url)
        self.assertNotIn("https://", out_text)
        self.assertIn("A", out_text)
        self.assertIn("B", out_text)

    def test_link_without_text_shows_url(self):
        out = convert('<a href="https://ex.com"></a>')
        self.assertEqual(out, "<https://ex.com>\n")


class TestInvariants(unittest.TestCase):
    COMPLEX_DOC = """
    <html><head><title>hidden title</title>
    <style>body { color: red; }</style></head>
    <body>
      <h1>Report &amp; Analysis</h1>
      <p>First paragraph with <b>bold</b> and <i>italic</i> words,
         plus a <a href="https://example.com/ref">reference link</a>.</p>
      <h2>Items</h2>
      <ul>
        <li>alpha
          <ol><li>alpha-one</li><li>alpha-two</li></ol>
        </li>
        <li>beta</li>
      </ul>
      <blockquote><p>Someone said something wise.</p></blockquote>
      <pre>code_line_1\n    indented_code()</pre>
      <table>
        <tr><th>ColA</th><th>ColB</th></tr>
        <tr><td>v1</td><td>v2</td></tr>
      </table>
      <script>var secret = "not-visible";</script>
    </body></html>
    """

    def test_content_conservation_url_mode(self):
        out = convert(self.COMPLEX_DOC, Config(link_mode="url"))
        assert_conserved(self, self.COMPLEX_DOC, out)

    def test_content_conservation_text_mode(self):
        out = convert(self.COMPLEX_DOC, Config(link_mode="text"))
        assert_conserved(self, self.COMPLEX_DOC, out)

    def test_hidden_content_not_in_output(self):
        out = convert(self.COMPLEX_DOC)
        self.assertNotIn("not-visible", out)
        self.assertNotIn("color: red", out)
        self.assertNotIn("hidden title", out)

    def test_determinism(self):
        for cfg in (Config(), Config(link_mode="text"),
                    Config(heading_style="plain")):
            first = convert(self.COMPLEX_DOC, cfg)
            for _ in range(5):
                self.assertEqual(convert(self.COMPLEX_DOC, cfg), first)

    def test_output_ends_with_single_newline(self):
        out = convert(self.COMPLEX_DOC)
        self.assertTrue(out.endswith("\n"))
        self.assertFalse(out.endswith("\n\n"))


class TestConfig(unittest.TestCase):
    def test_from_dict(self):
        cfg = Config.from_dict({"link_mode": "text",
                                "unordered_marker": "*"})
        self.assertEqual(cfg.link_mode, "text")
        self.assertEqual(cfg.unordered_marker, "*")

    def test_from_dict_rejects_unknown_keys(self):
        with self.assertRaises(ValueError):
            Config.from_dict({"nope": 1})

    def test_invalid_link_mode(self):
        with self.assertRaises(ValueError):
            Config(link_mode="both")

    def test_invalid_heading_style(self):
        with self.assertRaises(ValueError):
            Config(heading_style="fancy")

    def test_empty_separator_rejected(self):
        with self.assertRaises(ValueError):
            Config(table_column_separator="")


if __name__ == "__main__":
    unittest.main()
