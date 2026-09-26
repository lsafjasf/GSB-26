"""性能基准：十万条记录排序耗时。"""

import json
import random
import time

from collator import Collator

N = 100_000


def make_records(n, seed=7):
    rng = random.Random(seed)
    hanzi = list("安八拔把爸白北本才长成大的东都二发分高个国海好河红华会家江金九开可乐李林龙马美民明南年女朋平七青人日三山上生十水四他天王文五西下小新星学一英月云张中周子")
    latin = list("abcdeáàçéèABCÉ")
    records = []
    for _ in range(n):
        kind = rng.randrange(4)
        if kind == 0:
            s = "".join(rng.choice(hanzi) for _ in range(rng.randint(1, 3)))
        elif kind == 1:
            s = "".join(rng.choice(latin) for _ in range(rng.randint(3, 10)))
        elif kind == 2:
            s = rng.choice(latin) + str(rng.randint(0, 99999))
        else:
            s = "".join(rng.choice(hanzi) for _ in range(2)) + str(rng.randint(0, 999))
        records.append((s, rng.random()))
    return records


def main():
    with open("rules.json", encoding="utf-8") as f:
        collator = Collator(json.load(f))

    records = make_records(N)

    t0 = time.perf_counter()
    keys = [collator.sort_key(s) for s, _ in records]
    t1 = time.perf_counter()
    assert len(set(keys)) > N * 0.5  # 数据有足够区分度

    t2 = time.perf_counter()
    result = collator.sort(records, key=lambda r: r[0])
    t3 = time.perf_counter()

    # 校验有序性（抽样相邻对全量检查）
    for i in range(len(result) - 1):
        assert collator.compare(result[i][0], result[i + 1][0]) <= 0

    total = t3 - t2
    print("记录数:            %d" % N)
    print("排序键生成(仅参考): %.3f s" % (t1 - t0))
    print("排序总耗时:        %.3f s" % total)
    print("吞吐:              %.0f 条/秒" % (N / total))


if __name__ == "__main__":
    main()
