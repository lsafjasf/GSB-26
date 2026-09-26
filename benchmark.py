"""性能测试：十万条记录排序耗时，并输出未覆盖字符报告。"""

import random
import time

from localesort import LocaleSorter

N = 100_000
SEED = 20260926

CJK_POOL = list("中国北京上海广州深圳天津武汉西安南京杭州苏州成都重庆")
LATIN_POOL = ["apple", "banana", "Ábaco", "über", "école", "Zürich", "café",
              "naïve", "orange", "Çelik", "smörgås", "文件"]
UNCOVERED_POOL = ["龘", "龖", "🙂", "€", "Ω", "あ"]


def make_record(rng):
    kind = rng.random()
    if kind < 0.30:  # 中文名
        return "".join(rng.choice(CJK_POOL) for _ in range(rng.randint(2, 4)))
    if kind < 0.55:  # 拉丁词（含变音）
        return rng.choice(LATIN_POOL) + str(rng.randint(0, 99))
    if kind < 0.75:  # 含数字串
        return "item%d-%d" % (rng.randint(0, 10_000), rng.randint(0, 99))
    if kind < 0.85:  # 含符号
        return "%s %s.%s" % (rng.choice(LATIN_POOL), rng.choice("abc"),
                             rng.choice("xyz"))
    if kind < 0.95:  # 混合语言
        return rng.choice(LATIN_POOL) + "".join(
            rng.choice(CJK_POOL) for _ in range(2))
    # 含未覆盖字符
    return rng.choice(LATIN_POOL) + rng.choice(UNCOVERED_POOL) + str(
        rng.randint(0, 9))


def main():
    sorter = LocaleSorter.from_file("sort_rules.json")
    rng = random.Random(SEED)
    records = [make_record(rng) for _ in range(N)]

    sorter.reset_uncovered()
    start = time.perf_counter()
    out = sorter.sort(records)
    elapsed = time.perf_counter() - start

    # 校验有序性
    keys = [sorter.key(x) for x in out]
    assert all(keys[i] <= keys[i + 1] for i in range(len(keys) - 1))

    print("记录数: %d" % N)
    print("排序耗时: %.3f 秒 (sorted + key，单线程)" % (elapsed,))
    print("平均每条: %.2f µs" % (elapsed / N * 1e6))
    print("前 5 条:", out[:5])

    report = sorter.uncovered_report()
    with open("uncovered_report.txt", "w", encoding="utf-8") as fh:
        fh.write("# 未覆盖字符报告 (benchmark, n=%d)\n" % N)
        fh.write("# 这些字符不在 sort_rules.json 的映射表中，"
                 "按配置回退到末尾、彼此按码位排序\n")
        for ch, count in report.items():
            fh.write("U+%04X\t%s\t%d\n" % (ord(ch), ch, count))
    print("未覆盖字符 %d 种，详见 uncovered_report.txt" % len(report))


if __name__ == "__main__":
    main()
