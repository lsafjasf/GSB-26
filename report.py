"""生成检验报告样例：python3 report.py > report_sample.txt"""
import math
import platform
import sys
import time

from distfit import (Sampler, StdRandomSource, Uniform, Exponential, Normal,
                     LogNormal, Binomial, Poisson, goodness_of_fit,
                     quantile_comparison, quantile_test, fit, fit_best)


def section(title):
    print("\n" + "=" * 68)
    print(title)
    print("=" * 68)


def main():
    print("distfit 检验报告样例")
    print(f"Python {sys.version.split()[0]} / {platform.platform()}")
    print(f"生成时间: {time.strftime('%Y-%m-%d %H:%M:%S %Z')}")

    section("1. 六种分布采样 + 拟合优度检验 (n=5000, alpha=0.05, seed=2026)")
    cases = [
        ("uniform(-2, 5)", Uniform(-2, 5), lambda s: s.uniform(5000, -2, 5)),
        ("exponential(0.7)", Exponential(0.7), lambda s: s.exponential(5000, 0.7)),
        ("normal(3, 2.5)", Normal(3, 2.5), lambda s: s.normal(5000, 3, 2.5)),
        ("lognormal(0.5, 0.8)", LogNormal(0.5, 0.8),
         lambda s: s.lognormal(5000, 0.5, 0.8)),
        ("binomial(12, 0.4)", Binomial(12, 0.4), lambda s: s.binomial(5000, 12, 0.4)),
        ("poisson(4)", Poisson(4), lambda s: s.poisson(5000, 4.0)),
    ]
    for i, (label, dist, gen) in enumerate(cases):
        samples = gen(Sampler(StdRandomSource(2026 + i)))
        print(f"\n--- {label} ---")
        print(goodness_of_fit(samples, dist))

    section("2. 分位数对比 (normal(0,1), n=20000)")
    samples = Sampler(StdRandomSource(7)).normal(20000)
    print(f"{'q':>6} {'theoretical':>12} {'sample':>12} {'rel.err':>10}")
    for q, theo, samp, rel in quantile_comparison(samples, Normal(0, 1).ppf):
        print(f"{q:>6.2f} {theo:>12.4f} {samp:>12.4f} {rel:>10.4f}")

    section("3. 参数反推 (fit) + 拟合优度")
    fits = [
        ("uniform", lambda s: s.uniform(20000, -1.5, 4.0)),
        ("exponential", lambda s: s.exponential(20000, 2.0)),
        ("normal", lambda s: s.normal(20000, 1.0, 2.0)),
        ("lognormal", lambda s: s.lognormal(20000, 0.5, 0.8)),
        ("binomial", lambda s: s.binomial(20000, 15, 0.3)),
        ("poisson", lambda s: s.poisson(20000, 3.2)),
    ]
    for i, (family, gen) in enumerate(fits):
        print(f"\n--- fit '{family}' (真值见上) ---")
        print(fit(gen(Sampler(StdRandomSource(300 + i))), family))

    section("4. 明显不匹配的样本：必须拒绝")
    samples = Sampler(StdRandomSource(77)).exponential(5000, 1.0)
    print("--- 指数样本 exponential(1) 强行拟合 normal ---")
    print(fit(samples, "normal"))
    samples = Sampler(StdRandomSource(78)).uniform(5000, -math.sqrt(3), math.sqrt(3))
    print("\n--- 均匀样本(均值0方差1) 检验 normal(0,1)：均值相同但形状不同 ---")
    print(goodness_of_fit(samples, Normal(0, 1)))
    print("\n--- fit_best：正态样本自动选择分布族 ---")
    best = fit_best(Sampler(StdRandomSource(88)).normal(5000, 5, 1.5))
    for r in best:
        pv = f"{r.test.p_value:.4g}" if r.test.p_value == r.test.p_value else "NaN"
        print(f"  {r.family:<12} p-value={pv:<10} conclusion={r.test.conclusion}")

    section("5. 分位数对比检验 (quantile_test)")
    print("--- 正态样本 vs Normal(1,2)：应 accept ---")
    samples = Sampler(StdRandomSource(130)).normal(5000, 1, 2)
    print(quantile_test(samples, Normal(1, 2)))
    print("\n--- 指数样本 vs Normal(1,1)：应 reject ---")
    samples = Sampler(StdRandomSource(131)).exponential(5000, 1.0)
    print(quantile_test(samples, Normal(1, 1)))
    print("\n--- 对数正态样本 vs LogNormal(0,1)：应 accept ---")
    samples = Sampler(StdRandomSource(132)).lognormal(5000, 0.0, 1.0)
    print(quantile_test(samples, LogNormal(0, 1)))

    section("6. 小样本 (n=6) 与尾部极端值")
    small = Sampler(StdRandomSource(5)).normal(6)
    print(f"样本: {[round(x, 3) for x in small]}")
    print(goodness_of_fit(small, Normal(0, 1)))
    print("""
说明:
- n<30 时卡方分箱近似失效(每箱期望频数<5), 自动改用 KS 检验;
  n=6 时 KS 临界值很大(~0.52), 只有极端不匹配才能拒绝 -> 检验功效低,
  这是小样本的固有性质, 不是实现缺陷。
- 尾部极端值: 等概率分箱保证尾部箱与中部箱期望频数相同, 尾部偏差
  不会被稀释; 但指数/正态的极尾(如 p>0.999)在 n=5000 时每箱仅 ~5 个
  样本, 统计涨落大, 需更大样本才能分辨尾部偏差。
- 参数由样本估计时 KS 的 p 值偏保守(Lilliefors 情形), 报告已标注。""")


if __name__ == "__main__":
    main()
