"""benchmark.py — 增量更新 vs 整树重建耗时对比 + 证明长度与树高关系。
运行: python3 benchmark.py
"""

import math
import os
import random
import time

from merkle import MerkleTree

N = 100_000        # 数据块数量
OPS = 300          # 修改/追加操作次数
BLOCK = 64         # 数据块字节数


def bench_updates():
    blocks = [os.urandom(BLOCK) for _ in range(N)]
    tree = MerkleTree(blocks)
    rng = random.Random(42)
    indices = [rng.randrange(N) for _ in range(OPS)]
    new_blocks = [os.urandom(BLOCK) for _ in range(OPS)]

    t0 = time.perf_counter()
    for i, b in zip(indices, new_blocks):
        tree.update(i, b)
    t_inc = time.perf_counter() - t0

    t0 = time.perf_counter()
    for i, b in zip(indices, new_blocks):
        blocks[i] = b
        MerkleTree(blocks)  # 整树重建
    t_rebuild = time.perf_counter() - t0
    return t_inc, t_rebuild


def bench_appends():
    blocks = [os.urandom(BLOCK) for _ in range(N)]
    tree = MerkleTree(blocks)
    new_blocks = [os.urandom(BLOCK) for _ in range(OPS)]

    t0 = time.perf_counter()
    for b in new_blocks:
        tree.append(b)
    t_inc = time.perf_counter() - t0

    t0 = time.perf_counter()
    for b in new_blocks:
        blocks.append(b)
        MerkleTree(blocks)  # 整树重建
    t_rebuild = time.perf_counter() - t0
    return t_inc, t_rebuild


def proof_length_table():
    print("\n证明长度与树高关系（奇数节点采用复制末尾规则）：")
    print(f"{'块数 n':>10} {'树高 h':>8} {'证明长度':>8} {'ceil(log2 n)':>12} {'证明字节数':>10}")
    for n in (1, 2, 3, 4, 5, 8, 9, 16, 1000, 10_000, 100_000):
        t = MerkleTree([os.urandom(8) for _ in range(n)])
        plen = len(t.prove(n - 1))
        expect = 0 if n == 1 else math.ceil(math.log2(n))
        assert plen == t.height == expect
        print(f"{n:>10} {t.height:>8} {plen:>8} {expect:>12} {plen * 34:>10}")


def main():
    print(f"数据规模: {N:,} 块 x {BLOCK} 字节, 操作次数: {OPS}")
    t_inc, t_re = bench_updates()
    print(f"\n[修改 {OPS} 个数据块]")
    print(f"  增量更新（只重算路径）: {t_inc * 1e3:9.2f} ms  ({t_inc / OPS * 1e6:8.1f} us/次)")
    print(f"  整树重建              : {t_re * 1e3:9.2f} ms  ({t_re / OPS * 1e6:8.1f} us/次)")
    print(f"  加速比                : {t_re / t_inc:9.1f}x")

    t_inc, t_re = bench_appends()
    print(f"\n[追加 {OPS} 个数据块]")
    print(f"  增量追加（只重算路径）: {t_inc * 1e3:9.2f} ms  ({t_inc / OPS * 1e6:8.1f} us/次)")
    print(f"  整树重建              : {t_re * 1e3:9.2f} ms  ({t_re / OPS * 1e6:8.1f} us/次)")
    print(f"  加速比                : {t_re / t_inc:9.1f}x")

    proof_length_table()


if __name__ == "__main__":
    main()
