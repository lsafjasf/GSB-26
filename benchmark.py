"""性能基准：各分布采样 1,000,000 次的耗时。python3 benchmark.py"""
import time

from distfit import Sampler, StdRandomSource, Uniform, Exponential, Normal, Binomial

N = 1_000_000


def bench(label, fn):
    start = time.perf_counter()
    fn()
    elapsed = time.perf_counter() - start
    print(f"{label:<28} {elapsed:8.3f} s   ({elapsed / N * 1e9:7.1f} ns/sample)")
    return elapsed


def main():
    s = Sampler(StdRandomSource(12345))
    bench("uniform(0,1)", lambda: s.draw(Uniform(0, 1), N))
    bench("exponential(1)", lambda: s.draw(Exponential(1), N))
    bench("normal(0,1)", lambda: s.draw(Normal(0, 1), N))
    bench("binomial(10,0.3)", lambda: s.draw(Binomial(10, 0.3), N))
    bench("binomial(100,0.5)", lambda: s.draw(Binomial(100, 0.5), N))


if __name__ == "__main__":
    main()
