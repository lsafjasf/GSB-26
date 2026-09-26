"""内存与耗时基准：稀疏 (COO/CSR) vs 稠密。

运行: python3 bench.py
"""

import random
import time

from sparse import COOMatrix, CSRMatrix, FLOAT_SIZE, INT_SIZE


def make_random(nrows, ncols, density, seed):
    rng = random.Random(seed)
    rows, cols, vals = [], [], []
    nnz = int(nrows * ncols * density)
    seen = set()
    while len(seen) < nnz:
        k = rng.randrange(nrows * ncols)
        if k not in seen:
            seen.add(k)
            rows.append(k // ncols)
            cols.append(k % ncols)
            vals.append(rng.uniform(-1.0, 1.0))
    return COOMatrix(nrows, ncols, rows, cols, vals)


def fmt_bytes(n):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return "%.1f %s" % (n, unit) if unit != "B" else "%d B" % n
        n /= 1024.0


def timeit(fn, repeat=1):
    best = float("inf")
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t0)
    return best


def main():
    print("=" * 72)
    print("1) 内存对比（高度稀疏矩阵，dense 列为 float64 理论值）")
    print("=" * 72)
    header = "%-22s %12s %12s %12s %12s" % ("shape@density", "nnz", "COO", "CSR", "dense")
    print(header)
    print("-" * 72)
    cases = [
        (10_000, 10_000, 1e-4),
        (100_000, 100_000, 1e-5),
        (1_000_000, 1_000_000, 1e-6),
    ]
    for m, n, d in cases:
        coo = make_random(m, n, d, seed=42)
        csr = coo.to_csr()
        dense_bytes = m * n * FLOAT_SIZE  # 理论值，实际根本分配不起
        print("%-22s %12d %12s %12s %12s" % (
            "%dx%d@%.0e" % (m, n, d), coo.nnz,
            fmt_bytes(coo.memory_bytes()),
            fmt_bytes(csr.memory_bytes()),
            fmt_bytes(dense_bytes),
        ))
    print()
    print("稀疏格式内存 = O(nnz)，与矩阵边长无关；稠密 = O(m*n)。")
    print("100万阶、density=1e-6 时稠密需 8 TiB，而 CSR 仅约十几 MiB。")
    print()

    print("=" * 72)
    print("2) 耗时对比（2000x2000, density=0.01, nnz=40000；取 3 次最优）")
    print("=" * 72)
    m = n = 2000
    coo = make_random(m, n, 0.01, seed=7)
    csr = coo.to_csr()
    coo2 = make_random(m, n, 0.01, seed=8)
    csr2 = coo2.to_csr()
    dense = coo.to_dense()
    dense2 = coo2.to_dense()
    x = [random.random() for _ in range(n)]

    def dense_matvec():
        return [sum(row[j] * x[j] for j in range(n)) for row in dense]

    def dense_add():
        return [[a + b for a, b in zip(ra, rb)] for ra, rb in zip(dense, dense2)]

    def dense_transpose():
        return [list(c) for c in zip(*dense)]

    rows_out = [
        ("matvec", timeit(lambda: csr.matvec(x), 3),
         timeit(lambda: coo.matvec(x), 3), timeit(dense_matvec, 3)),
        ("add", timeit(lambda: csr.add(csr2), 3),
         timeit(lambda: coo.add(coo2), 3), timeit(dense_add, 3)),
        ("transpose", timeit(lambda: csr.transpose(), 3),
         timeit(lambda: coo.transpose(), 3), timeit(dense_transpose, 3)),
    ]
    print("%-12s %12s %12s %12s" % ("op", "CSR (ms)", "COO (ms)", "dense (ms)"))
    print("-" * 72)
    for name, t_csr, t_coo, t_dense in rows_out:
        print("%-12s %12.2f %12.2f %12.2f" % (
            name, t_csr * 1e3, t_coo * 1e3, t_dense * 1e3))
    print()
    print("稀疏耗时 = O(nnz)，稠密 = O(m*n)；nnz 越小稀疏优势越大。")
    print("注：纯 Python 稠密实现无 SIMD，实际 BLAS 稠密会快得多，")
    print("但 O(m*n) 与 O(nnz) 的量级差异不变。")
    print()

    print("=" * 72)
    print("3) 近稠密取舍：稀疏格式每非零元的存储开销")
    print("=" * 72)
    per_nnz_coo = 2 * INT_SIZE + FLOAT_SIZE
    per_nnz_csr = INT_SIZE + FLOAT_SIZE
    print("COO 每元素 %d B，CSR 每元素 %d B（另加每行 %d B），稠密每元素 %d B。"
          % (per_nnz_coo, per_nnz_csr, INT_SIZE, FLOAT_SIZE))
    print("CSR 存储盈亏平衡密度 ≈ %d/%d ≈ %.0f%%；超过该密度稠密更省内存，"
          % (FLOAT_SIZE, per_nnz_csr, 100.0 * FLOAT_SIZE / per_nnz_csr))
    print("且稠密可借助连续内存 + SIMD/BLAS，运算吞吐更高。")
    for d in (0.1, 0.5, 0.66, 1.0):
        m2 = n2 = 10_000
        nnz = int(m2 * n2 * d)
        csr_b = (m2 + 1) * INT_SIZE + nnz * per_nnz_csr
        dense_b = m2 * n2 * FLOAT_SIZE
        print("  density=%4.2f  CSR=%s  dense=%s  %s" % (
            d, fmt_bytes(csr_b), fmt_bytes(dense_b),
            "CSR 省" if csr_b < dense_b else "dense 省"))


if __name__ == "__main__":
    main()
