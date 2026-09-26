"""Tests for the incremental tokenizer.

Key guarantee under test: after ANY edit (insert / delete / replace /
undo / redo), the incrementally maintained token stream and per-line
entry states must be IDENTICAL to a from-scratch tokenization of the
new text.
"""

import random
import unittest

from incremental_tokenizer import (
    Document, full_tokenize, NORMAL, BLOCK_COMMENT, TRIPLE_STRING,
)


def offset_to_pos(text, off):
    line = text.count("\n", 0, off)
    col = off - (text.rfind("\n", 0, off) + 1)
    return (line, col)


def assert_consistent(testcase, doc, expected_text):
    testcase.assertEqual(doc.text, expected_text)
    fresh = Document(expected_text)
    testcase.assertEqual(doc.tokens(), fresh.tokens(),
                         "token stream diverged from full re-tokenization")
    testcase.assertEqual(doc.line_states(), fresh.line_states(),
                         "per-line entry states diverged")


class EdgeCaseTests(unittest.TestCase):
    def test_empty_document(self):
        doc = Document("")
        self.assertEqual(doc.tokens(), [])
        doc.insert((0, 0), "x = 1")
        assert_consistent(self, doc, "x = 1")
        doc.undo()
        assert_consistent(self, doc, "")
        doc.redo()
        assert_consistent(self, doc, "x = 1")

    def test_single_line_no_trailing_newline(self):
        doc = Document("if x: return 3.14")
        assert_consistent(self, doc, "if x: return 3.14")
        doc.insert((0, 17), " // tail")
        assert_consistent(self, doc, "if x: return 3.14 // tail")

    def test_very_long_line(self):
        line = "x" * 99990 + " /* mid */ " + "y" * 99998
        doc = Document(line)
        assert_consistent(self, doc, line)
        doc.insert((0, 50000), "1")
        assert_consistent(self, doc, line[:50000] + "1" + line[50000:])
        self.assertEqual(doc.last_retokenized_lines, 1)
        doc.delete((0, 0), (0, 10))
        assert_consistent(self, doc, (line[:50000] + "1" + line[50000:])[10:])

    def test_edit_at_file_start(self):
        text = '"""doc\nstring"""\nx = 1\n'
        doc = Document(text)
        doc.insert((0, 0), "/*")
        assert_consistent(self, doc, "/*" + text)
        doc.undo()
        assert_consistent(self, doc, text)

    def test_edit_at_file_end(self):
        text = "x = 1\ny = 2\n"
        doc = Document(text)
        doc.insert((2, 0), "/* unterminated")
        assert_consistent(self, doc, text + "/* unterminated")
        doc.insert((2, 15), " */")
        assert_consistent(self, doc, text + "/* unterminated */")
        doc.undo()
        doc.undo()
        assert_consistent(self, doc, text)

    def test_unterminated_constructs_at_eof(self):
        doc = Document("a = 1\n/*")
        self.assertEqual(doc.line_states(), [NORMAL, NORMAL])
        assert_consistent(self, doc, "a = 1\n/*")
        doc.delete((1, 0), (1, 2))
        assert_consistent(self, doc, "a = 1\n")


class PropagationBoundaryTests(unittest.TestCase):
    """Verify the exact re-tokenization boundary when multi-line
    constructs are opened, closed, or merely touched."""

    def make_doc(self):
        # 100 code lines; a block comment spanning lines 20..30.
        lines = ["x%d = %d" % (i, i) for i in range(100)]
        lines[20] = "a = 1 /* comment opens"
        lines[25] = "still inside the comment"
        lines[30] = "comment closes */ b = 2"
        return Document("\n".join(lines))

    def test_edit_outside_construct_is_one_line(self):
        doc = self.make_doc()
        doc.insert((50, 0), "z")
        self.assertEqual(doc.last_retokenized_lines, 1)

    def test_edit_inside_comment_respans_construct_only(self):
        doc = self.make_doc()
        doc.insert((25, 3), "XYZ")
        # walk back to opener (line 20), stop at line 31 (state NORMAL
        # matches cached) -> lines 20..30 inclusive = 11 lines.
        self.assertEqual(doc.last_retokenized_lines, 11)
        assert_consistent(self, doc, doc.text)

    def test_deleting_closer_propagates_to_next_close_or_eof(self):
        doc = self.make_doc()
        # remove "*/" on line 30: comment now runs to EOF (no other */).
        line = doc.text.split("\n")[30]
        col = line.index("*/")
        doc.delete((30, col), (30, col + 2))
        # re-tokenized from opener line 20 to last line 99 -> 80 lines.
        self.assertEqual(doc.last_retokenized_lines, 100 - 20)
        self.assertEqual(doc.line_states()[50], BLOCK_COMMENT)
        assert_consistent(self, doc, doc.text)

    def test_opening_comment_propagates_to_its_close(self):
        doc = self.make_doc()
        # type "/*" at start of line 40: swallows code until the closer
        # on line 30? no - forward: until next "*/" which is on line 30
        # (already passed) -> actually the next */ after line 40 does not
        # exist, so it runs to EOF.
        doc.insert((40, 0), "/*")
        self.assertEqual(doc.last_retokenized_lines, 100 - 40)
        self.assertEqual(doc.line_states()[60], BLOCK_COMMENT)
        doc.undo()  # closing it again must restore exactly
        self.assertEqual(doc.line_states()[60], NORMAL)
        assert_consistent(self, doc, doc.text)

    def test_open_and_close_triple_string(self):
        lines = ["s = 1", "t = 2", "u = 3", "v = 4"]
        doc = Document("\n".join(lines))
        doc.insert((0, 0), '"""')
        # entry states: lines 1..3 are now inside the triple string
        self.assertEqual(doc.line_states(),
                         [NORMAL, TRIPLE_STRING, TRIPLE_STRING, TRIPLE_STRING])
        doc.insert((2, 5), '"""')  # close it at end of line 2
        self.assertEqual(doc.line_states(),
                         [NORMAL, TRIPLE_STRING, TRIPLE_STRING, NORMAL])
        assert_consistent(self, doc, doc.text)
        doc.undo()
        doc.undo()
        assert_consistent(self, doc, "\n".join(lines))


class DifferentialTests(unittest.TestCase):
    """Randomized edits; after every single edit the incremental result
    is compared token-by-token against a full re-tokenization."""

    PIECES = [
        "a", "foo", "_x9", "1", "3.14", " ", "  ", "\n", "\n\n",
        "/*", "*/", "/* */", '"', '"str"', "\\", '"""', "//", "// c\n",
        "if ", "else ", "return ", "+", "=", "(", ")", "*/ /*", 'a"""b',
        "x = 1\n", "/* c\n", "\n*/", 's = "a\\"b"\n',
    ]

    def random_edit(self, rng, doc, mirror):
        op = rng.random()
        if op < 0.45 or not mirror:  # insert
            off = rng.randint(0, len(mirror))
            pos = offset_to_pos(mirror, off)
            text = rng.choice(self.PIECES)
            doc.insert(pos, text)
            mirror = mirror[:off] + text + mirror[off:]
        elif op < 0.8:  # delete
            a = rng.randint(0, len(mirror) - 1)
            b = min(len(mirror), a + rng.randint(1, 12))
            doc.delete(offset_to_pos(mirror, a), offset_to_pos(mirror, b))
            mirror = mirror[:a] + mirror[b:]
        else:  # replace
            a = rng.randint(0, len(mirror) - 1)
            b = min(len(mirror), a + rng.randint(0, 8))
            text = rng.choice(self.PIECES)
            doc.replace(offset_to_pos(mirror, a), offset_to_pos(mirror, b), text)
            mirror = mirror[:a] + text + mirror[b:]
        return mirror

    def test_random_edits_match_full_tokenization(self):
        for seed in range(8):
            rng = random.Random(seed)
            mirror = "\n".join(
                rng.choice(self.PIECES) for _ in range(rng.randint(0, 60)))
            doc = Document(mirror)
            assert_consistent(self, doc, mirror)
            for step in range(250):
                mirror = self.random_edit(rng, doc, mirror)
                try:
                    assert_consistent(self, doc, mirror)
                except AssertionError as e:
                    raise AssertionError(
                        "seed=%d step=%d: %s" % (seed, step, e))

    def test_undo_redo_match_full_tokenization(self):
        rng = random.Random(1234)
        mirror = "/* a\nb */\nx = 1\n"
        doc = Document(mirror)
        snapshots = [mirror]
        for _ in range(120):
            mirror = self.random_edit(rng, doc, mirror)
            snapshots.append(mirror)
        # undo everything, checking each intermediate state
        for k in range(len(snapshots) - 1, 0, -1):
            self.assertTrue(doc.undo())
            assert_consistent(self, doc, snapshots[k - 1])
        self.assertFalse(doc.undo())
        # redo everything, checking again
        for k in range(1, len(snapshots)):
            self.assertTrue(doc.redo())
            assert_consistent(self, doc, snapshots[k])
        self.assertFalse(doc.redo())
        # interleaved undo/redo with fresh edits
        for _ in range(60):
            r = rng.random()
            if r < 0.4:
                doc.undo()
            elif r < 0.7:
                doc.redo()
            else:
                mirror = self.random_edit(rng, doc, doc.text)
            assert_consistent(self, doc, doc.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
