"""顺序相关性分析：对比固定顺序与打乱顺序两种模式下每个测试的失败次数。

若某测试在固定顺序下从不失败、在打乱顺序下频繁失败（或反之），
则其失败很可能与执行顺序相关（典型原因：测试间共享状态污染）。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OrderEffect:
    test_id: str
    fixed_runs: int
    fixed_failures: int
    shuffled_runs: int
    shuffled_failures: int

    @property
    def fixed_rate(self) -> float:
        return self.fixed_failures / self.fixed_runs if self.fixed_runs else 0.0

    @property
    def shuffled_rate(self) -> float:
        return self.shuffled_failures / self.shuffled_runs if self.shuffled_runs else 0.0

    @property
    def order_dependent(self) -> bool:
        # 两种模式失败率差超过 20% 且至少一侧有 >=2 次失败，判定为顺序相关
        diff = abs(self.fixed_rate - self.shuffled_rate)
        return diff > 0.2 and max(self.fixed_failures, self.shuffled_failures) >= 2


def analyze_order(records: list[dict]) -> list[OrderEffect]:
    stats: dict[str, dict[str, list[int]]] = {}
    for rec in records:
        get = rec.get if isinstance(rec, dict) else lambda k: getattr(rec, k)
        per_mode = stats.setdefault(get("test_id"),
                                    {"fixed": [0, 0], "shuffled": [0, 0]})
        bucket = per_mode[get("mode")]
        bucket[0] += 1
        if get("status") != "pass":
            bucket[1] += 1
    effects = []
    for tid, per_mode in sorted(stats.items()):
        fr, ff = per_mode["fixed"]
        sr, sf = per_mode["shuffled"]
        effects.append(OrderEffect(tid, fr, ff, sr, sf))
    return effects
