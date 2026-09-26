"""Benchmark: re-tokenization scope and edit latency.

Scenarios (run on a 100,000-line document unless noted):
  * single-char insert in the middle (normal code)  -> must be O(1) lines
  * single-char insert inside a block comment       -> bounded by construct
  * deleting a block-comment closer                 -> propagates to next close
  * edits at file start / file end
  * single very long line
  * undo / redo
"""

import statistics
import time

from incremental_tokenizer import Document

LINES = 100_000


def build_doc():
    lines = []
    i = 0
    while i < LINES:
        if i % 1000 == 500:
            lines.append("x = 1 /* block comment opens")
            for k in range(1, 19):
                lines.append("comment body line %d" % k)
            lines.append("comment closes */ y = 2")
            i += 20
        else:
            lines.append('def f%d(a, b):  # c%d\n    return a + %d  // tail'
                         % (i, i, i) if False else
                         'value%d = compute(%d, "s%d") + %d  // c%d'
                         % (i, i, i, i, i))
            i += 1
    return "\n".join(lines)


def timed(fn, repeat=1):
    best = float("inf")
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t0)
    return best * 1e3  # ms


def main():
    print("building %d-line document ..." % LINES)
    t0 = time.perf_counter()
    text = build_doc()
    doc = Document(text)
    print("full tokenize: %.1f ms, %d tokens, %d lines\n"
          % ((time.perf_counter() - t0) * 1e3, len(doc.tokens()),
             doc.line_count))

    mid = LINES // 2  # normal code line
    cmt = 505         # inside the block comment spanning 500..519

    # 1. single char insert, middle of file, normal code
    ms = timed(lambda: doc.insert((mid, 5), "Q"))
    print("insert 1 char @ line %d (code)     : %7.3f ms | re-tokenized %d line(s)"
          % (mid, ms, doc.last_retokenized_lines))
    assert doc.last_retokenized_lines <= 2
    ms = timed(lambda: doc.undo())
    print("undo that insert                   : %7.3f ms | re-tokenized %d line(s)"
          % (ms, doc.last_retokenized_lines))

    # 2. single char insert inside a 20-line block comment
    ms = timed(lambda: doc.insert((cmt, 3), "Q"))
    print("insert 1 char inside block comment : %7.3f ms | re-tokenized %d line(s)"
          % (ms, doc.last_retokenized_lines))
    doc.undo()

    # 3. delete the block-comment closer -> propagation to the NEXT closer
    line500 = doc.text.split("\n")[519]
    col = line500.index("*/")
    ms = timed(lambda: doc.delete((519, col), (519, col + 2)))
    print("delete '*/' closer @ line 519      : %7.3f ms | re-tokenized %d line(s)"
          % (ms, doc.last_retokenized_lines))
    ms = timed(lambda: doc.undo())
    print("undo (restore closer)              : %7.3f ms | re-tokenized %d line(s)"
          % (ms, doc.last_retokenized_lines))

    # 4. edits at file start / end
    ms = timed(lambda: doc.insert((0, 0), "/*"))
    print("insert '/*' at file start          : %7.3f ms | re-tokenized %d line(s)"
          % (ms, doc.last_retokenized_lines))
    doc.undo()
    last = doc.line_count - 1
    ms = timed(lambda: doc.insert((last, 0), "z = 1"))
    print("insert text at file end            : %7.3f ms | re-tokenized %d line(s)"
          % (ms, doc.last_retokenized_lines))
    doc.undo()

    # 5. average latency of 200 random single-char inserts (code lines)
    import random
    rng = random.Random(7)
    samples = []
    for _ in range(200):
        ln = rng.randrange(doc.line_count)
        t0 = time.perf_counter()
        doc.insert((ln, 0), "w")
        samples.append(time.perf_counter() - t0)
        doc.undo()
    print("random 1-char inserts x200         : avg %.3f ms | p50 %.3f | max %.3f"
          % (statistics.mean(samples) * 1e3,
             statistics.median(samples) * 1e3, max(samples) * 1e3))

    # 6. single very long line (500k chars)
    long_doc = Document("x" * 499_990 + " /* c */ " + "y" * 100_000)
    ms = timed(lambda: long_doc.insert((0, 250_000), "1"))
    print("insert into 600k-char single line  : %7.3f ms | re-tokenized %d line(s)"
          % (ms, long_doc.last_retokenized_lines))

    # 7. redo
    ms = timed(lambda: doc.redo())
    print("redo (nothing to redo -> no-op)    : %7.3f ms" % ms)

    print("\nrequirement check: middle-of-100k-lines single-char insert "
          "re-tokenizes <= 2 lines (<< %d)  OK" % LINES)


if __name__ == "__main__":
    main()
