"""encsniff 基准: 误判率（短文本 / 混合编码样本）与大文件吞吐。

运行: python3 benchmark.py
"""

import random
import time

from encsniff import SniffError, convert, sniff

ZH = ("编码嗅探的核心原则是宁可拒绝也不可猜错，因为猜错会把内容静默破坏，"
      "这比直接报错要糟糕得多。导入外部文件时编码未知，必须谨慎判定。")
FR = ("L'encodage des fichiers texte est une source classique d'erreurs: "
      "à bientôt, garçon! Noël, naïve, œuvre, façade, déjà vu, très élégant.")
EN = "Plain ASCII text without any surprises, used as a control group. "

CORPUS = {
    "utf-8": (ZH + FR).encode("utf-8"),
    "utf-16-le": (ZH + EN).encode("utf-16-le"),
    "gbk": ZH.encode("gbk"),
    "cp1252": FR.encode("cp1252"),
}


def _char_aligned_slices(data: bytes, encoding: str, n: int, rng) -> list:
    """从文本中随机切出 1~8 个字符的短样本（字符边界对齐）。"""
    text = data.decode(encoding)
    slices = []
    for _ in range(n):
        start = rng.randrange(len(text))
        length = rng.randint(1, 8)
        slices.append(text[start:start + length].encode(encoding))
    return slices


def bench_short_text(n_per_encoding: int = 400, seed: int = 42):
    rng = random.Random(seed)
    print(f"\n== 短文本误判率（每编码 {n_per_encoding} 个 1~8 字符样本）==")
    print(f"{'真实编码':<12}{'正确':>8}{'拒绝':>8}{'误判':>8}{'误判率':>10}")
    total_wrong = total = 0
    for name, data in CORPUS.items():
        ok = rejected = wrong = 0
        for sample in _char_aligned_slices(data, name, n_per_encoding, rng):
            try:
                result = sniff(sample)
            except SniffError:
                rejected += 1
                continue
            got = result.encoding
            # 纯 ASCII 切片判为 utf-8 与任何 ASCII 兼容编码等价，算正确。
            ascii_equiv = got == "utf-8" and all(b < 0x80 for b in sample)
            if got == name or ascii_equiv:
                ok += 1
            else:
                wrong += 1
        total_wrong += wrong
        total += n_per_encoding
        print(f"{name:<12}{ok:>8}{rejected:>8}{wrong:>8}{wrong / n_per_encoding:>9.2%}")
    print(f"{'总计':<12}{'':>8}{'':>8}{total_wrong:>8}{total_wrong / total:>9.2%}")


def bench_mixed(n: int = 300, seed: int = 7):
    """混合编码样本: 两种编码拼接，正确行为是拒绝（不存在单一正确编码）。"""
    rng = random.Random(seed)
    zh_gbk = ZH.encode("gbk")
    zh_utf8 = ZH.encode("utf-8")
    fr_cp = FR.encode("cp1252")
    pairs = [("gbk+cp1252", zh_gbk, fr_cp), ("utf-8+gbk", zh_utf8, zh_gbk)]
    print(f"\n== 混合编码样本（每种组合 {n} 个，正确行为 = 拒绝）==")
    print(f"{'组合':<14}{'拒绝(正确)':>12}{'误判接受':>12}{'误判率':>10}")
    for label, a, b in pairs:
        rejected = wrong = 0
        for _ in range(n):
            la = rng.randint(20, 200)
            lb = rng.randint(20, 200)
            sa = a[:la]
            sb = b[:lb]
            sample = sa + sb if rng.random() < 0.5 else sb + sa
            try:
                sniff(sample)
                wrong += 1
            except SniffError:
                rejected += 1
        print(f"{label:<14}{rejected:>12}{wrong:>12}{wrong / n:>9.2%}")


def bench_throughput(size_mb: int = 64):
    print(f"\n== 大文件吞吐（目标 {size_mb} MiB）==")
    print(f"{'编码':<12}{'大小 MiB':>10}{'嗅探 s':>10}{'转换 s':>10}{'吞吐 MiB/s':>12}")
    for name, unit in (
        ("utf-8", ZH.encode("utf-8")),
        ("gbk", ZH.encode("gbk")),
        ("utf-16-le", ZH.encode("utf-16-le")),
        ("ascii", EN.encode("ascii")),
    ):
        reps = max(1, (size_mb << 20) // len(unit))
        data = unit * reps
        mb = len(data) / (1 << 20)
        t0 = time.perf_counter()
        result = sniff(data)
        t1 = time.perf_counter()
        out = convert(data, source_encoding=result.encoding)
        t2 = time.perf_counter()
        assert out.decode("utf-8").encode(result.encoding) == data  # 顺带验证无损
        print(f"{name:<12}{mb:>10.1f}{t1 - t0:>10.3f}{t2 - t1:>10.3f}"
              f"{mb / (t2 - t1):>12.0f}")


if __name__ == "__main__":
    bench_short_text()
    bench_mixed()
    bench_throughput()
