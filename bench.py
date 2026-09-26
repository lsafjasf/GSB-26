"""性能基准：十万行文档上的重分词范围与编辑响应耗时。"""

import random
import time

from incremental_lexer import IncrementalLexer, full_tokenize

N = 100_000


def make_plain():
    return "\n".join("int value_%d = %d;" % (i, i * 7) for i in range(N))


def make_with_closers():
    return "\n".join("int value_%d = %d; /* c%d */" % (i, i * 7, i)
                     for i in range(N))


def timed(fn, repeat=1):
    best = float("inf")
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t0)
    return best


def main():
    print("文档规模: %d 行" % N)
    print("-" * 72)

    # 初始全量分词
    text = make_plain()
    t = timed(lambda: IncrementalLexer(text))
    print("初始全量分词（10 万行）          : %8.1f ms" % (t * 1e3))

    lx = IncrementalLexer(text)
    mid = N // 2

    # 1) 中间插入一个字符
    t = timed(lambda: (lx.insert(mid, 4, "x"), lx.undo()), repeat=5)
    lx.insert(mid, 4, "x")
    r1 = lx.last_relex_lines
    lx.undo()
    print("中间插入 1 个字符              : 重分词 %d 行, 编辑+重分词 %.3f ms"
          % (r1, t * 1e3))

    # 2) 中间删除一个字符
    t = timed(lambda: (lx.delete((mid, 4), (mid, 5)), lx.undo()), repeat=5)
    lx.delete((mid, 4), (mid, 5))
    r2 = lx.last_relex_lines
    lx.undo()
    print("中间删除 1 个字符              : 重分词 %d 行, 编辑+重分词 %.3f ms"
          % (r2, t * 1e3))

    # 3) 随机单字符编辑 1000 次取平均
    rng = random.Random(7)
    lx2 = IncrementalLexer(text)
    t0 = time.perf_counter()
    total_lines = 0
    for _ in range(1000):
        line = rng.randrange(N)
        col = rng.randrange(len(lx2.lines[line]) + 1)
        lx2.insert(line, col, "x")
        total_lines += lx2.last_relex_lines
    dt = (time.perf_counter() - t0) / 1000
    assert lx2.tokens() == full_tokenize(lx2.text())
    print("随机单字符插入 x1000（含对拍） : 平均重分词 %.1f 行, 平均耗时 %.3f ms"
          % (total_lines / 1000, dt * 1e3))

    # 4) 整段替换（跨 20 行）
    seg = "\n".join("float r%d = %d.5;" % (i, i) for i in range(20))
    t = timed(lambda: (lx.replace((mid, 0), (mid + 19, 15), seg), lx.undo()),
              repeat=5)
    lx.replace((mid, 0), (mid + 19, 15), seg)
    r3 = lx.last_relex_lines
    lx.undo()
    print("整段替换 20 行                 : 重分词 %d 行, 耗时 %.3f ms"
          % (r3, t * 1e3))

    # 5) 打开块注释：同行下方存在 */，传播被立即截断
    lx3 = IncrementalLexer(make_with_closers())
    lx3.insert(mid, 0, "/*")
    r4 = lx3.last_relex_lines
    t = timed(lambda: (lx3.undo(), lx3.redo()), repeat=5)
    print("插入 /*（同行有 */ 闭合）      : 重分词 %d 行, undo+redo %.3f ms"
          % (r4, t * 1e3))

    # 6) 打开块注释：下方无 */，传播至文末（最坏情况）
    lx4 = IncrementalLexer(text)
    t = timed(lambda: (lx4.insert(mid, 0, "/*"), lx4.undo()), repeat=5)
    lx4.insert(mid, 0, "/*")
    r5 = lx4.last_relex_lines
    lx4.undo()
    print("插入 /*（传播至文末，最坏）    : 重分词 %d 行, 耗时 %.3f ms"
          % (r5, t * 1e3))

    # 7) 文件开头 / 结尾编辑
    lx5 = IncrementalLexer(text)
    t = timed(lambda: (lx5.insert(0, 0, "x"), lx5.undo()), repeat=5)
    print("文件开头插入 1 字符            : 重分词 1 行, 耗时 %.3f ms" % (t * 1e3))
    t = timed(lambda: (lx5.insert(N - 1, 15, "x"), lx5.undo()), repeat=5)
    print("文件结尾插入 1 字符            : 重分词 1 行, 耗时 %.3f ms" % (t * 1e3))

    # 8) 超长行
    long_line = "int x = " + " + ".join(str(i) for i in range(100_000)) + ";"
    lx6 = IncrementalLexer(long_line)
    print("超长行（1 行 %.1f 万字符）分词   : 初始 %.1f ms"
          % (len(long_line) / 1e4,
             timed(lambda: IncrementalLexer(long_line)) * 1e3))
    midc = len(long_line) // 2
    t = timed(lambda: (lx6.insert(0, midc, "7"), lx6.undo()), repeat=5)
    print("超长行中间插入 1 字符          : 重分词 1 行, 耗时 %.3f ms" % (t * 1e3))

    # 9) 撤销/重做链（每次开关注释都会触发传播+回滚）
    lx7 = IncrementalLexer(text)
    t0 = time.perf_counter()
    for _ in range(50):
        lx7.insert(mid, 0, "//")
        lx7.undo()
        lx7.redo()
        lx7.undo()
    print("撤销/重做 200 步（开关注释）   : 总耗时 %.1f ms"
          % ((time.perf_counter() - t0) * 1e3))


if __name__ == "__main__":
    main()
