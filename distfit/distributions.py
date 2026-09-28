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

    def pdf(self, x):
        return 1.0 / (self.b - self.a) if self.a <= x <= self.b else 0.0

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

    def pdf(self, x):
        return self.lam * math.exp(-self.lam * x) if x >= 0.0 else 0.0

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

    def pdf(self, x):
        z = (x - self.mu) / self.sigma
        return math.exp(-0.5 * z * z) / (self.sigma * math.sqrt(2.0 * math.pi))

    def cdf(self, x):
        return special.normal_cdf(x, self.mu, self.sigma)

    def ppf(self, p):
        return special.normal_ppf(p, self.mu, self.sigma)

    def params(self):
        return {"mu": self.mu, "sigma": self.sigma}


class LogNormal(Distribution):
    """对数正态：ln(X) ~ N(mu, sigma^2)，支撑 x > 0。

    边界行为：sigma 必须 > 0（sigma=0 为零方差退化，拒绝构造）；
    mu 必须有限；sigma 极端小/大都允许，采样恒为正有限值。
    """
    name = "lognormal"
    n_params = 2

    def __init__(self, mu=0.0, sigma=1.0):
        if not math.isfinite(mu):
            raise ValueError("lognormal mu must be finite")
        if not (math.isfinite(sigma) and sigma > 0.0):
            raise ValueError("lognormal requires sigma > 0")
        self.mu, self.sigma = float(mu), float(sigma)
        self._normal = Normal(mu, sigma)

    def sample(self, source):
        return math.exp(self._normal.sample(source))

    def pdf(self, x):
        if x <= 0.0:
            return 0.0
        z = (math.log(x) - self.mu) / self.sigma
        return math.exp(-0.5 * z * z) / (x * self.sigma * math.sqrt(2.0 * math.pi))

    def cdf(self, x):
        if x <= 0.0:
            return 0.0
        return special.normal_cdf(math.log(x), self.mu, self.sigma)

    def ppf(self, p):
        return math.exp(special.normal_ppf(p, self.mu, self.sigma))

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

    def support(self):
        return range(0, self.n + 1)

    def params(self):
        return {"n": self.n, "p": self.p}


class Poisson(Distribution):
    """泊松分布：lam >= 0。

    边界行为：
    - lam = 0 为零方差退化分布，采样恒为 0（不消耗随机源），pmf(0)=1。
    - lam < 10 用 Knuth 连乘法（O(lam)）；lam >= 10 用 Hörmann PTRS
      变换拒绝法（O(1)），lam 高达 1e6 以上仍精确高效。
    - lam 为负、NaN 或 inf 时拒绝构造。
    """
    name = "poisson"
    n_params = 1
    discrete = True

    _PTRS_THRESHOLD = 10.0

    def __init__(self, lam=1.0):
        if not (math.isfinite(lam) and lam >= 0.0):
            raise ValueError("poisson requires finite lam >= 0")
        self.lam = float(lam)

    def sample(self, source):
        if self.lam == 0.0:
            return 0
        if self.lam < self._PTRS_THRESHOLD:
            # Knuth：均匀数连乘，首次乘积 <= e^-lam 时的次数减一
            threshold = math.exp(-self.lam)
            k = 0
            p = 1.0
            while p > threshold:
                k += 1
                p *= source.random()
            return k - 1
        return self._sample_ptrs(source)

    def _sample_ptrs(self, source):
        # Hörmann (1993) PTRS，与 numpy 的 rk_poisson_ptrs 同算法
        lam = self.lam
        loglam = math.log(lam)
        b = 0.931 + 2.53 * math.sqrt(lam)
        a = -0.059 + 0.02483 * b
        inv_alpha = 1.1239 + 1.1328 / (b - 3.4)
        v_r = 0.9277 - 3.6224 / (b - 2)
        while True:
            u = source.random() - 0.5
            v = source.random()
            us = 0.5 - abs(u)
            if us <= 0.0:
                continue
            k = int(math.floor((2.0 * a / us + b) * u + lam + 0.43))
            if us >= 0.07 and v <= v_r:
                return k
            if k < 0 or (us < 0.013 and v > us):
                continue
            log_v = math.log(v) if v > 0.0 else -math.inf
            if (log_v + math.log(inv_alpha) - math.log(a / (us * us) + b)
                    <= -lam + k * loglam - math.lgamma(k + 1)):
                return k

    def pmf(self, k):
        k = int(math.floor(k))
        if k < 0:
            return 0.0
        if self.lam == 0.0:
            return 1.0 if k == 0 else 0.0
        return math.exp(-self.lam + k * math.log(self.lam)
                        - math.lgamma(k + 1))

    def cdf(self, x):
        k = int(math.floor(x))
        if k < 0:
            return 0.0
        return min(1.0, sum(self.pmf(i) for i in range(k + 1)))

    def ppf(self, p):
        acc = 0.0
        k = 0
        while True:
            acc += self.pmf(k)
            if acc >= p:
                return k
            k += 1

    def support(self):
        # 截断到 1 - 1e-12 分位点，覆盖全部有效质量
        return range(0, self.ppf(1.0 - 1e-12) + 1)

    def params(self):
        return {"lam": self.lam}


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

    def poisson(self, count, lam=1.0):
        return self.draw(Poisson(lam), count)

    def lognormal(self, n, mu=0.0, sigma=1.0):
        return self.draw(LogNormal(mu, sigma), n)
