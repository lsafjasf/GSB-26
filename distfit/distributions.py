"""分布定义与采样器。

采样器只依赖一个可注入的随机源（任何提供 random() -> [0,1) 浮点的对象），
因此可以用固定种子的 StdRandomSource 复现结果，也可以换成自定义 RNG
（如确定性序列、加密源等）做测试。
"""
import math
import random

from . import special


class StdRandomSource:
    """默认随机源：包装 random.Random，可用种子复现。"""

    def __init__(self, seed=None):
        self._rng = random.Random(seed)

    def random(self):
        return self._rng.random()


class Distribution:
    """分布基类：sample 由注入的随机源驱动；cdf/ppf 用于分布检验。"""
    name = "abstract"
    n_params = 0
    discrete = False

    def sample(self, source):
        raise NotImplementedError

    def cdf(self, x):
        raise NotImplementedError

    def ppf(self, p):
        raise NotImplementedError

    def params(self):
        raise NotImplementedError


class Uniform(Distribution):
    name = "uniform"
    n_params = 2

    def __init__(self, a=0.0, b=1.0):
        if not (math.isfinite(a) and math.isfinite(b)):
            raise ValueError("uniform bounds must be finite")
        if not a < b:
            raise ValueError("uniform requires a < b")
        self.a, self.b = float(a), float(b)

    def sample(self, source):
        return self.a + (self.b - self.a) * source.random()

    def cdf(self, x):
        if x <= self.a:
            return 0.0
        if x >= self.b:
            return 1.0
        return (x - self.a) / (self.b - self.a)

    def ppf(self, p):
        return self.a + (self.b - self.a) * p

    def params(self):
        return {"a": self.a, "b": self.b}


class Exponential(Distribution):
    name = "exponential"
    n_params = 1

    def __init__(self, lam=1.0):
        if not (math.isfinite(lam) and lam > 0.0):
            raise ValueError("exponential requires lam > 0")
        self.lam = float(lam)

    def sample(self, source):
        u = source.random()
        return -math.log(1.0 - u) / self.lam

    def cdf(self, x):
        if x <= 0.0:
            return 0.0
        return 1.0 - math.exp(-self.lam * x)

    def ppf(self, p):
        return -math.log(1.0 - p) / self.lam

    def params(self):
        return {"lam": self.lam}


class Normal(Distribution):
    name = "normal"
    n_params = 2

    def __init__(self, mu=0.0, sigma=1.0):
        if not math.isfinite(mu):
            raise ValueError("normal mu must be finite")
        if not (math.isfinite(sigma) and sigma > 0.0):
            raise ValueError("normal requires sigma > 0")
        self.mu, self.sigma = float(mu), float(sigma)
        self._spare = None

    def sample(self, source):
        # Box-Muller，缓存另一半
        if self._spare is not None:
            z, self._spare = self._spare, None
            return self.mu + self.sigma * z
        u1 = 1.0 - source.random()  # 避免 log(0)
        u2 = source.random()
        r = math.sqrt(-2.0 * math.log(u1))
        self._spare = r * math.sin(2.0 * math.pi * u2)
        return self.mu + self.sigma * r * math.cos(2.0 * math.pi * u2)

    def cdf(self, x):
        return special.normal_cdf(x, self.mu, self.sigma)

    def ppf(self, p):
        return special.normal_ppf(p, self.mu, self.sigma)

    def params(self):
        return {"mu": self.mu, "sigma": self.sigma}


class Binomial(Distribution):
    name = "binomial"
    n_params = 2
    discrete = True

    def __init__(self, n, p):
        if not isinstance(n, int) or isinstance(n, bool) or n < 0:
            raise ValueError("binomial requires integer n >= 0")
        if not (0.0 <= p <= 1.0):
            raise ValueError("binomial requires 0 <= p <= 1")
        self.n, self.p = n, float(p)

    def sample(self, source):
        # 伯努利试验求和；n 大时可用几何等待时间优化，此处保持简单正确
        count = 0
        for _ in range(self.n):
            if source.random() < self.p:
                count += 1
        return count

    def pmf(self, k):
        if k < 0 or k > self.n:
            return 0.0
        if self.p == 0.0:
            return 1.0 if k == 0 else 0.0
        if self.p == 1.0:
            return 1.0 if k == self.n else 0.0
        log_pmf = (math.lgamma(self.n + 1) - math.lgamma(k + 1)
                   - math.lgamma(self.n - k + 1)
                   + k * math.log(self.p) + (self.n - k) * math.log1p(-self.p))
        return math.exp(log_pmf)

    def cdf(self, x):
        k = int(math.floor(x))
        if k < 0:
            return 0.0
        if k >= self.n:
            return 1.0
        return sum(self.pmf(i) for i in range(k + 1))

    def ppf(self, p):
        acc = 0.0
        for k in range(self.n + 1):
            acc += self.pmf(k)
            if acc >= p:
                return k
        return self.n

    def params(self):
        return {"n": self.n, "p": self.p}


class Sampler:
    """采样器：所有采样都经过注入的 source，保证可复现、可替换。"""

    def __init__(self, source=None):
        self.source = source if source is not None else StdRandomSource()

    def draw(self, dist, n):
        if not isinstance(n, int) or n < 0:
            raise ValueError("sample count must be a non-negative integer")
        return [dist.sample(self.source) for _ in range(n)]

    def uniform(self, n, a=0.0, b=1.0):
        return self.draw(Uniform(a, b), n)

    def exponential(self, n, lam=1.0):
        return self.draw(Exponential(lam), n)

    def normal(self, n, mu=0.0, sigma=1.0):
        return self.draw(Normal(mu, sigma), n)

    def binomial(self, count, n, p):
        return self.draw(Binomial(n, p), count)
