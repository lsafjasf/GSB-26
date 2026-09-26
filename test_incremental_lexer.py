"""增量分词器自测：增量结果与全文重分词逐 token 对拍。"""

import random
import unittest

from incremental_lexer import IncrementalLexer, full_tokenize


def make_lexer(text):
    return IncrementalLexer(text)


class DiffMixin:
    def assertSync(self, lx):
        """增量 token 流必须与全文重分词完全一致（类型+起止位置逐条对拍）。"""
        inc = lx.tokens()
        full = full_tokenize(lx.text())
        self.assertEqual(
            inc, full,
            msg="\n".join(
                ["incremental vs full mismatch, text=%r" % lx.text()]
                + ["  inc : %r" % t for t in inc]
                + ["  full: %r" % t for t in full]))


class TestBasic(DiffMixin, unittest.TestCase):
    def check(self, text):
        self.assertSync(make_lexer(text))

    def test_empty_document(self):
        lx = make_lexer("")
        self.assertEqual(lx.tokens(), [])
        lx.insert(0, 0, "x")
        self.assertSync(lx)
        lx.delete((0, 0), (0, 1))
        self.assertEqual(lx.tokens(), [])
        self.assertSync(lx)

    def test_single_line(self):
        self.check('int main() { return 0; } // tail')

    def test_constructs(self):
        self.check(
            'int a = 0x1; /* block\n'
            'still comment */ float b = 1.5;\n'
            'char *s = "he\\"llo";\n'
            'char *t = `multi\n'
            'line `str` end`;\n'
            'if (a) { b++; } // done')

    def test_unterminated_constructs(self):
        self.check('/* never closed\nint a = 1;\nfloat b = 2;')
        self.check('`open string\nint a = 1;')
        self.check('char *s = "unterminated')

    def test_super_long_line(self):
        line = "int x = " + " + ".join(str(i) for i in range(20000)) + ";"
        lx = make_lexer(line)
        self.assertSync(lx)
        mid = len(line) // 2
        lx.insert(0, mid, "7")          # 超长行中间插入
        self.assertSync(lx)
        self.assertEqual(lx.last_relex_lines, 1)
        lx.delete((0, mid), (0, mid + 1))
        self.assertSync(lx)

    def test_edit_at_file_start(self):
        lx = make_lexer("int a = 1;\nint b = 2;\n")
        lx.insert(0, 0, "//")           # 开头变行注释
        self.assertSync(lx)
        lx.undo()
        self.assertSync(lx)
        lx.insert(0, 0, "/*")           # 开头打开块注释，吞掉全文
        self.assertSync(lx)
        self.assertEqual(lx.last_relex_lines, 3)

    def test_edit_at_file_end(self):
        lx = make_lexer("int a = 1;\nint b = 2;")
        last = len(lx.lines) - 1
        lx.insert(last, len(lx.lines[last]), " // eof")
        self.assertSync(lx)
        self.assertEqual(lx.last_relex_lines, 1)
        lx.insert(last, len(lx.lines[last]), "\nint c = 3;")  # 末尾追加新行
        self.assertSync(lx)

    def test_single_char_insert_delete(self):
        lx = make_lexer("int foo = 42;")
        lx.insert(0, 4, "b")            # 单字符插入
        self.assertSync(lx)
        lx.delete((0, 4), (0, 5))       # 单字符删除
        self.assertSync(lx)
        self.assertEqual(lx.text(), "int foo = 42;")

    def test_block_replace(self):
        lx = make_lexer("int a = 1;\nint b = 2;\nint c = 3;\nint d = 4;")
        lx.replace((1, 0), (2, 9), "/* x\ny */ float z = 0;")  # 跨行整段替换
        self.assertSync(lx)
        lx.undo()
        self.assertSync(lx)
        lx.redo()
        self.assertSync(lx)

    def test_undo_redo_chain(self):
        lx = make_lexer("int a = 1;")
        snapshots = [lx.text()]
        edits = [
            lambda: lx.insert(0, 0, "/*"),
            lambda: lx.insert(0, 12, "*/"),
            lambda: lx.replace((0, 2), (0, 5), "//"),
            lambda: lx.insert(0, 0, "int head;\n"),
        ]
        for fn in edits:
            fn()
            self.assertSync(lx)
            snapshots.append(lx.text())
        for snap in reversed(snapshots[:-1]):
            lx.undo()
            self.assertEqual(lx.text(), snap)
            self.assertSync(lx)
        for snap in snapshots[1:]:
            lx.redo()
            self.assertEqual(lx.text(), snap)
            self.assertSync(lx)


class TestPropagationBoundary(DiffMixin, unittest.TestCase):
    """跨行结构打开/闭合时，验证重分词传播边界精确停在状态重新同步的行。"""

    DOC = "\n".join("int v%d = %d;" % (i, i) for i in range(10))

    def test_open_block_comment_propagates_to_eof(self):
        lx = make_lexer(self.DOC)
        lx.insert(3, 0, "/*")           # 第 3 行打开块注释，下方无 */
        self.assertSync(lx)
        self.assertEqual(lx.last_relex_lines, 7)   # 第 3..9 行全部被重分词
        self.assertTrue(all(t.type == "COMMENT" for t in lx.tokens() if t.line >= 3))

    def test_open_block_comment_stops_at_closer(self):
        doc = self.DOC.split("\n")
        doc[7] = "int v7 = 7; /* tail */ int z = 0;"
        lx = make_lexer("\n".join(doc))
        before = lx.tokens()
        lx.insert(3, 0, "/*")
        self.assertSync(lx)
        # 传播边界：第 3..7 行重分词（第 7 行行末状态恢复 NORMAL），第 8 行起不动
        self.assertEqual(lx.last_relex_lines, 5)
        after = lx.tokens()
        self.assertEqual([t for t in before if t.line >= 8],
                         [t for t in after if t.line >= 8])

    def test_close_block_comment_propagates_back(self):
        lx = make_lexer(self.DOC)
        lx.insert(3, 0, "/*")
        self.assertSync(lx)
        lx.delete((3, 0), (3, 2))       # 删掉 /*，结构闭合
        self.assertSync(lx)
        self.assertEqual(lx.last_relex_lines, 7)   # 同样传播到文末重新同步
        self.assertEqual(lx.text(), self.DOC)

    def test_close_at_same_line_stops_immediately(self):
        lx = make_lexer(self.DOC)
        lx.insert(3, 0, "/*")
        lx.insert(9, len(lx.lines[9]), "*/")  # 在末行闭合
        self.assertSync(lx)
        self.assertEqual(lx.last_relex_lines, 1)   # 只影响最后一行
        lx.delete((9, len(lx.lines[9]) - 2), (9, len(lx.lines[9])))
        self.assertSync(lx)
        self.assertEqual(lx.last_relex_lines, 1)

    def test_multiline_string_propagation(self):
        # 反引号开闭符相同，行状态是奇偶翻转：插入单个 ` 后下方每行的
        # 新末态都与旧末态相反，不存在重新同步点，必然传播至文末。
        lx = make_lexer(self.DOC)
        lx.insert(2, 0, "`")
        self.assertSync(lx)
        self.assertEqual(lx.last_relex_lines, 8)   # 第 2..9 行
        self.assertTrue(all(t.type == "STRING"
                            for t in lx.tokens() if t.line >= 3))
        lx.delete((2, 0), (2, 1))       # 删除反引号，结构闭合
        self.assertSync(lx)
        self.assertEqual(lx.last_relex_lines, 8)
        self.assertEqual(lx.text(), self.DOC)

    def test_edit_inside_multiline_string_is_local(self):
        doc = self.DOC.split("\n")
        doc[4] = "char *s = `start"     # 第 4..6 行是一个多行字符串
        doc[6] = "end` + tail;"
        lx = make_lexer("\n".join(doc))
        lx.insert(5, 3, "x")            # 字符串内部编辑：末态不变，立即停
        self.assertSync(lx)
        self.assertEqual(lx.last_relex_lines, 1)
        lx.delete((6, 3), (6, 4))       # 删掉闭合反引号：字符串延伸到文末
        self.assertSync(lx)
        self.assertEqual(lx.last_relex_lines, 4)   # 第 6..9 行

    def test_no_propagation_for_local_edit(self):
        lx = make_lexer(self.DOC)
        lx.insert(5, 4, "x")            # 普通编辑：只重分词一行
        self.assertSync(lx)
        self.assertEqual(lx.last_relex_lines, 1)


class TestFuzz(DiffMixin, unittest.TestCase):
    """随机文档 + 随机编辑 + 撤销重做，每步与全量分词对拍。"""

    PIECES = ["int ", "x", " = ", "42", ";", " ", "/*", "*/", "//", '"', "`",
              "\\", "\n", "if", "(", ")", "foo", "1.5", "\n\n", "*/ /*"]

    def random_text(self, rng, n):
        return "".join(rng.choice(self.PIECES) for _ in range(n))

    def random_pos(self, rng, lx):
        line = rng.randrange(len(lx.lines))
        return (line, rng.randrange(len(lx.lines[line]) + 1))

    def test_fuzz(self):
        for seed in range(40):
            rng = random.Random(seed)
            lx = make_lexer(self.random_text(rng, 30))
            self.assertSync(lx)
            for _ in range(120):
                op = rng.randrange(6)
                if op == 0:
                    p = self.random_pos(rng, lx)
                    lx.insert(p[0], p[1], rng.choice(self.PIECES))
                elif op == 1:
                    p = self.random_pos(rng, lx)
                    lx.insert(p[0], p[1], self.random_text(rng, 3))
                elif op in (2, 3):
                    a, b = self.random_pos(rng, lx), self.random_pos(rng, lx)
                    if a > b:
                        a, b = b, a
                    if op == 2:
                        lx.delete(a, b)
                    else:
                        lx.replace(a, b, self.random_text(rng, 2))
                elif op == 4:
                    lx.undo()
                else:
                    lx.redo()
                self.assertSync(lx)


if __name__ == "__main__":
    unittest.main(verbosity=2)
