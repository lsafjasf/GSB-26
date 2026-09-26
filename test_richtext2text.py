"""richtext2text 自测：规则、不变量与边界情形。"""

import re
import unittest

from richtext2text import Config, convert, visible_text

WS_RE = re.compile(r"\s+")


def squash(text):
    """去掉全部空白后的字符序列。"""
    return WS_RE.sub("", text)


def is_subsequence(needle, haystack):
    it = iter(haystack)
    return all(ch in it for ch in needle)


def assert_conserved(testcase, html, config=None):
    """不变量：源文档所有可见文本字符按顺序出现在输出中。

    输出允许额外出现结构性字符（列表标记、#、|、>、URL 等），
    但源文档可见文本（script/style/head 之外）去掉空白后，
    必须是输出去掉空白后的子序列。
    """
    out = convert(html, config)
    src_chars = squash(visible_text(html))
    out_chars = squash(out)
    testcase.assertTrue(
        is_subsequence(src_chars, out_chars),
        "可见文本未守恒：源可见文本不是输出的子序列")
    return out


class TestBasic(unittest.TestCase):
    def test_empty_document(self):
        self.assertEqual(convert(""), "")
        self.assertEqual(convert("   \n\t  "), "")
        self.assertEqual(convert("<div></div><p> </p>"), "")

    def test_plain_text(self):
        self.assertEqual(convert("hello world"), "hello world\n")
        self.assertEqual(convert("a   b\n\nc"), "a b c\n")  # 空白折叠

    def test_paragraphs_separated_by_blank_line(self):
        out = convert("<p>one</p><p>two</p>")
        self.assertEqual(out, "one\n\ntwo\n")

    def test_headings(self):
        out = convert("<h1>A</h1><h2>B</h2><h6>C</h6>")
        self.assertEqual(out, "# A\n\n## B\n\n###### C\n")

    def test_br_and_entities(self):
        out = convert("<p>a<br>b &amp; c&nbsp;d</p>")
        self.assertEqual(out, "a\nb & c d\n")

    def test_script_style_head_dropped(self):
        html = ("<head><title>t</title><style>css{}</style></head>"
                "<p>keep</p><script>var x=1;</script>")
        out = convert(html)
        self.assertEqual(out, "keep\n")

    def test_html_body_wrapper_keeps_structure(self):
        html = ("<html><body><h1>T</h1><ul><li>a</li></ul>"
                "<table><tr><td>x</td></tr></table></body></html>")
        out = convert(html)
        self.assertEqual(out, "# T\n\n- a\n\nx\n")

    def test_hr(self):
        self.assertEqual(convert("<p>a</p><hr><p>b</p>"), "a\n\n---\n\nb\n")


class TestLists(unittest.TestCase):
    def test_unordered(self):
        out = convert("<ul><li>a</li><li>b</li></ul>")
        self.assertEqual(out, "- a\n- b\n")

    def test_ordered_numbering(self):
        out = convert("<ol><li>a</li><li>b</li><li>c</li></ol>")
        self.assertEqual(out, "1. a\n2. b\n3. c\n")

    def test_ordered_start_attribute(self):
        out = convert('<ol start="5"><li>a</li><li>b</li></ol>')
        self.assertEqual(out, "5. a\n6. b\n")

    def test_nested_levels_and_indent(self):
        html = ("<ul><li>a<ul><li>a1<ol><li>deep</li></ol></li></ul></li>"
                "<li>b</li></ul>")
        out = convert(html)
        self.assertEqual(out,
                         "- a\n"
                         "  - a1\n"
                         "    1. deep\n"
                         "- b\n")

    def test_deeply_nested_list(self):
        depth = 50
        html = "leaf"
        for _ in range(depth):
            html = f"<ul><li>{html}</li></ul>"
        out = convert(html)
        last = [ln for ln in out.splitlines() if ln.strip()][-1]
        self.assertEqual(last, "  " * (depth - 1) + "- leaf")
        assert_conserved(self, html)

    def test_list_item_with_multiple_blocks(self):
        html = "<ul><li><p>p1</p><p>p2</p></li></ul>"
        out = convert(html)
        self.assertEqual(out, "- p1\n\n  p2\n")


class TestQuoteAndCode(unittest.TestCase):
    def test_blockquote(self):
        out = convert("<blockquote><p>q1</p><p>q2</p></blockquote>")
        self.assertEqual(out, "> q1\n\n> q2\n")

    def test_nested_blockquote(self):
        out = convert("<blockquote><p>a</p><blockquote><p>b</p></blockquote></blockquote>")
        self.assertEqual(out, "> a\n\n> > b\n")

    def test_pre_preserved_verbatim(self):
        code = "def f(x):\n    if x:\n        return  x+1"
        out = convert(f"<pre>{code}</pre>")
        self.assertEqual(out, code + "\n")

    def test_pre_not_reflowed(self):
        code = "a   b\t\tc\n\n\nindented    line"
        out = convert(f"<pre>{code}</pre>")
        self.assertIn("a   b\t\tc", out)
        self.assertIn("indented    line", out)

    def test_inline_code_is_text(self):
        out = convert("<p>use <code>print()</code> here</p>")
        self.assertEqual(out, "use print() here\n")


class TestTable(unittest.TestCase):
    def test_basic_table(self):
        html = ("<table><tr><th>Name</th><th>Age</th></tr>"
                "<tr><td>Al</td><td>3</td></tr>"
                "<tr><td>Bo</td><td>4</td></tr></table>")
        out = convert(html)
        self.assertEqual(out,
                         "Name | Age\n"
                         "--- | ---\n"
                         "Al | 3\n"
                         "Bo | 4\n")

    def test_ragged_rows_padded(self):
        html = "<table><tr><td>a</td><td>b</td></tr><tr><td>c</td></tr></table>"
        out = convert(html)
        self.assertEqual(out, "a | b\nc | \n")

    def test_wide_table(self):
        cols = 200
        header = "".join(f"<th>h{i}</th>" for i in range(cols))
        row = "".join(f"<td>v{i}</td>" for i in range(cols))
        html = f"<table><tr>{header}</tr><tr>{row}</tr></table>"
        out = convert(html)
        lines = out.splitlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0].count(" | "), cols - 1)
        self.assertEqual(lines[1], " | ".join(["---"] * cols))
        assert_conserved(self, html)

    def test_caption(self):
        html = "<table><caption>cap</caption><tr><td>x</td></tr></table>"
        out = convert(html)
        self.assertEqual(out, "cap\nx\n")


class TestLinks(unittest.TestCase):
    HTML = '<p>see <a href="https://ex.com/a">doc</a> and ' \
           '<a href="https://ex.com/b">https://ex.com/b</a></p>'

    def test_url_mode(self):
        out = convert(self.HTML, Config(link_mode="url"))
        self.assertIn("doc (https://ex.com/a)", out)
        self.assertIn("https://ex.com/b", out)
        self.assertNotIn("(https://ex.com/b)", out)  # 文本==URL 不重复

    def test_text_mode(self):
        out = convert(self.HTML, Config(link_mode="text"))
        self.assertIn("doc", out)
        self.assertNotIn("https://ex.com/a", out)

    def test_mode_consistent_across_document(self):
        html = self.HTML * 20
        out_url = convert(html, Config(link_mode="url"))
        out_txt = convert(html, Config(link_mode="text"))
        self.assertEqual(out_url.count("https://ex.com/a"), 20)
        self.assertNotIn("https://ex.com/a", out_txt)

    def test_invalid_config_rejected(self):
        with self.assertRaises(ValueError):
            Config(link_mode="keep")


class TestInvariants(unittest.TestCase):
    RICH = """
    <html><head><title>ignored</title></head><body>
    <h1>报告 &amp; 摘要</h1>
    <p>第一段，含 <b>加粗</b>、<i>斜体</i> 与
       <a href="https://example.com/ref">参考资料</a>。</p>
    <ul><li>要点一</li><li>要点二<ol start="3"><li>子项甲</li></ol></li></ul>
    <blockquote><p>引文：不可<span>拆分</span>。</p></blockquote>
    <pre>line1\n  line2   keep</pre>
    <table><tr><th>列A</th><th>列B</th></tr>
    <tr><td>1</td><td>2</td></tr></table>
    </body></html>
    """

    def test_content_conservation_url_mode(self):
        assert_conserved(self, self.RICH, Config(link_mode="url"))

    def test_content_conservation_text_mode(self):
        assert_conserved(self, self.RICH, Config(link_mode="text"))

    def test_determinism(self):
        a = convert(self.RICH)
        b = convert(self.RICH)
        self.assertEqual(a, b)

    def test_malformed_html(self):
        html = "<ul><li>one<li>two<ol><li>sub</ol><p>para<b>bold"
        out = assert_conserved(self, html)
        self.assertIn("- one", out)
        self.assertIn("1. sub", out)

    def test_no_triple_newlines(self):
        out = convert(self.RICH)
        self.assertNotIn("\n\n\n", out)
        self.assertTrue(out.endswith("\n"))


if __name__ == "__main__":
    unittest.main()
