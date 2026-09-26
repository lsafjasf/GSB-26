"""稳定性判定：根据重复次数与失败分布给出「稳定通过 / 稳定失败 / 不稳定」判定。

判定规则（默认阈值，可用参数覆盖）：
  - n 次执行中失败 k 次：
    - k == 0   -> stable_pass（稳定通过）
    - k == n   -> stable_fail（稳定失败）
    - 0 < k < n -> flaky（不稳定）
  - 置信度（对 stable_* 判定）：
      假设真实失败率至少为 p0（默认 0.05，即“最低值得关心的失败率”），
      那么 n 次全通过（或全失败）的概率为 (1-p0)^n。
      置信度 = 1 - (1-p0)^n，表示“真实失败率低于 p0”的可信程度。
      例：n=60, p0=0.05 -> 置信度 95.4%。
  - 对 flaky 判定：报告失败率及其 Wilson 95% 置信区间。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

DEFAULT_P0 = 0.05
Z_95 = 1.96


def wilson_interval(k: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """二项分布比例的 Wilson 置信区间。"""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


@dataclass
class Verdict:
    test_id: str
    runs: int
    failures: int
    errors: int
    verdict: str            # stable_pass | stable_fail | flaky
    failure_rate: float
    confidence: float       # stable_* 判定的置信度；flaky 时为 NaN
    ci_low: float           # 失败率 Wilson 95% 区间
    ci_high: float
    avg_duration: float
    p0: float

    def explain(self) -> str:
        base = (
            f"{self.test_id}: {self.verdict} "
            f"(runs={self.runs}, failures={self.failures}, errors={self.errors}, "
            f"failure_rate={self.failure_rate:.3f} "
            f"95%CI=[{self.ci_low:.3f},{self.ci_high:.3f}], "
            f"avg_duration={self.avg_duration*1000:.2f}ms)"
        )
        if self.verdict == "flaky":
            return base + f"\n    判定依据: 0 < failures < runs，同一测试出现两种结果"
        return base + (
            f"\n    判定依据: {self.runs} 次结果一致；若真实失败率>={self.p0}，"
            f"出现该结果的概率仅 {(1-self.p0)**self.runs:.4f}，"
            f"故置信度 {self.confidence:.4f}"
        )


def classify(test_id: str, statuses: list[str], durations: list[float],
             p0: float = DEFAULT_P0) -> Verdict:
    n = len(statuses)
    failures = sum(1 for s in statuses if s == "fail")
    errors = sum(1 for s in statuses if s == "error")
    bad = failures + errors
    rate = bad / n if n else 0.0
    ci_low, ci_high = wilson_interval(bad, n)
    confidence = 1 - (1 - p0) ** n
    if bad == 0:
        verdict = "stable_pass"
    elif bad == n:
        verdict = "stable_fail"
    else:
        verdict = "flaky"
    avg = sum(durations) / n if n else 0.0
    return Verdict(test_id, n, failures, errors, verdict, rate,
                   confidence, ci_low, ci_high, avg, p0)


def judge_records(records: list[dict], p0: float = DEFAULT_P0) -> list[Verdict]:
    by_test: dict[str, tuple[list[str], list[float]]] = {}
    for rec in records:
        get = rec.get if isinstance(rec, dict) else lambda k: getattr(rec, k)
        statuses, durations = by_test.setdefault(get("test_id"), ([], []))
        statuses.append(get("status"))
        durations.append(get("duration"))
    return [classify(tid, s, d, p0) for tid, (s, d) in sorted(by_test.items())]
