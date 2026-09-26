"""内存与耗时基准：高度稀疏 vs 近稠密，COO/CSR/稠密三种表示对比。仅标准库。"""

import random
import sys
import time

from sparse import COOMatrix, CSRMatrix, DenseMatrix


def deep_size(obj, _seen=None):
    """递归估算对象内存占用（字节）。"""
    if _seen is None:
        _seen = set()
    if id(obj) in _seen:
        return 0
    _seen.add(id(obj))
    size = sys.getsizeof(obj)
    if isinstance(obj, (list, tuple)):
        size += sum(deep_size(x, _seen) for x in obj)
    elif isinstance(obj, object) and hasattr(obj, "__slots__"):
        for name in obj.__slots__:
            size += deep_size(getattr(obj, name), _seen)
    return size


def make_random_coo(rng, n, density):
    rows, cols, vals = [], [], []
    nnz_target = int(n * n * density)
    for _ in range(nnz_target):
        rows.append(rng.randrange(n))
        cols.append(rng.randrange(n))
        vals.append(rng.uniform(-1, 1))
    return COOMatrix(n, n, rows, cols, vals)


def bench_case(name, n, density, reps=5):
    rng = random.Random(0)
    coo = make_random_coo(rng, n, density)
    csr = coo.to_csr()
    nnz = csr.nnz

    mem_coo = deep_size(coo)
    mem_csr = deep_size(csr)

    x = [rng.uniform(-1, 1) for _ in range(n)]

    t0 = time.perf_counter()
    for _ in range(reps):
        csr.matvec(x)
    t_csr = (time.perf_counter() - t0) / reps

    # 稠密参照（规模可控时）
    if n * n <= 4_000_000:
        dense = csr.to_dense()
        mem_dense = deep_size(dense)
        t0 = time.perf_counter()
        for _ in range(reps):
            dense.matvec(x)
        t_dense = (time.perf_counter() - t0) / reps
        dense_mem_s = f"{mem_dense/1e6:10.1f}"
        dense_t_s = f"{t_dense*1e3:10.2f}"
    else:
        mem_dense = 8 * n * n  # 估算：double 8 字节
        dense_mem_s = f"~{mem_dense/1e6:9.1f}"
        dense_t_s = "    (跳过)"

    print(f"[{name}] n={n}, density={density:.0e}, nnz={nnz:,}")
    print(f"  内存(MB)   COO {mem_coo/1e6:10.2f} | CSR {mem_csr/1e6:10.2f} "
          f"| 稠密 {dense_mem_s}")
    print(f"  matvec(ms) CSR {t_csr*1e3:14.3f} | 稠密 {dense_t_s}")
    print(f"  稀疏/稠密内存比(CSR): {mem_csr/mem_dense:.4f}")
    print()


def main():
    print("== 高度稀疏：不得退化为稠密 ==\n")
    bench_case("超稀疏", n=20_000, density=1e-4)   # nnz ≈ 4e4，稠密需 ~3.2GB
    bench_case("稀疏",   n=2_000,  density=1e-3)

    print("== 近稠密：稀疏格式的取舍 ==\n")
    bench_case("近稠密", n=1_000, density=0.9)

    print("== 其他运算耗时（n=2000, density=1e-3）==\n")
    rng = random.Random(1)
    a = make_random_coo(rng, 2000, 1e-3).to_csr()
    b = make_random_coo(rng, 2000, 1e-3).to_csr()
    for label, fn in [("COO->CSR 转换", lambda: a.to_coo().to_csr()),
                      ("CSR 转置", lambda: a.transpose()),
                      ("CSR 加法", lambda: a.add(b))]:
        t0 = time.perf_counter()
        for _ in range(10):
            fn()
        print(f"  {label}: {(time.perf_counter()-t0)/10*1e3:.3f} ms")


if __name__ == "__main__":
    main()
