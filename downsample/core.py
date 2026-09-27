"""时序指标降采样核心库（仅依赖标准库）。

口径约定（详细见 README.md）：
- 时间戳为整数，窗口大小 window 为正整数，与时间戳同单位。
- 窗口网格对齐到绝对原点 0：第 k 个窗口为 [k*window, (k+1)*window)，左闭右开。
  恰好落在边界上的点归入右侧窗口。
- 输入点可乱序、可重复时间戳；重复时间戳的每个样本都独立参与聚合。
- 无采样点的窗口 value=None、count=0，绝不静默补零或插值。
- 聚合按指标类型区分口径：可加（sum/count）与不可加（max/avg/quantile），
  混用（合并不同口径的部分聚合状态、或合并不可加指标的部分状态）抛
  MixedAggregationError。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

Point = Tuple[int, float]


class MixedAggregationError(TypeError):
    """可加与不可加聚合口径混用，或合并不同聚合器的状态。"""


class Aggregator:
    """聚合器基类。子类通过状态（state）增量累积样本，最后 finalize 出值。"""

    name = "abstract"
    additive = False  # 是否可加：部分聚合状态可否安全合并

    @property
    def params(self) -> tuple:
        return ()

    def new_state(self):
        return []

    def add(self, state, value: float) -> None:
        state.append(value)

    def count(self, state) -> int:
        return len(state)

    def merge(self, state_a, state_b):
        raise MixedAggregationError(
            f"{self.name} 为不可加指标，禁止合并部分聚合状态"
        )

    def finalize(self, state) -> float:
        raise NotImplementedError

    def finalize_values(self, values: Iterable[float]) -> Optional[float]:
        vals = list(values)
        if not vals:
            return None
        state = self.new_state()
        for v in vals:
            self.add(state, v)
        return self.finalize(state)


class Sum(Aggregator):
    """可加：计数/求和类指标。用 math.fsum 保证与到达顺序无关的精确结果。"""

    name = "sum"
    additive = True

    def merge(self, state_a, state_b):
        return state_a + state_b

    def finalize(self, state) -> float:
        return math.fsum(state)


class Count(Aggregator):
    """可加：样本计数。状态为单元素列表 [n]，与其余聚合器接口统一。"""

    name = "count"
    additive = True

    def new_state(self):
        return [0]

    def add(self, state, value: float) -> None:
        state[0] += 1

    def count(self, state) -> int:
        return state[0]

    def merge(self, state_a, state_b):
        return [state_a[0] + state_b[0]]

    def finalize(self, state) -> float:
        return float(state[0])


class Max(Aggregator):
    """不可加：最大值。"""

    name = "max"

    def finalize(self, state) -> float:
        return max(state)


class Avg(Aggregator):
    """不可加：平均值。fsum(state)/n，与到达顺序无关。"""

    name = "avg"

    def finalize(self, state) -> float:
        return math.fsum(state) / len(state)


class Quantile(Aggregator):
    """不可加：分位数。排序后按 q*(n-1) 线性插值，结果与到达顺序无关。"""

    name = "quantile"

    def __init__(self, q: float):
        if not 0.0 <= q <= 1.0:
            raise ValueError("q 必须在 [0, 1] 内")
        self.q = float(q)

    @property
    def params(self) -> tuple:
        return (self.q,)

    def finalize(self, state) -> float:
        vals = sorted(state)
        n = len(vals)
        pos = self.q * (n - 1)
        lo = int(pos)
        hi = min(lo + 1, n - 1)
        frac = pos - lo
        return vals[lo] * (1.0 - frac) + vals[hi] * frac


@dataclass(frozen=True)
class Bucket:
    """一个降采样窗口。value 为 None 表示窗口内无采样点（空洞）。"""

    start: int
    end: int
    value: Optional[float]
    count: int


def merge_states(agg_a: Aggregator, state_a, agg_b: Aggregator, state_b):
    """合并两个部分聚合状态。

    - 两个聚合器口径不同（类型或参数不同）-> MixedAggregationError
    - 不可加指标 -> MixedAggregationError
    """
    if type(agg_a) is not type(agg_b) or agg_a.params != agg_b.params:
        raise MixedAggregationError(
            f"不同聚合口径的状态禁止合并: {agg_a.name}{agg_a.params} vs "
            f"{agg_b.name}{agg_b.params}"
        )
    if not agg_a.additive:
        raise MixedAggregationError(
            f"{agg_a.name} 为不可加指标，禁止合并部分聚合状态"
        )
    return agg_a.merge(state_a, state_b)


def downsample(
    points: Iterable[Point],
    window: int,
    aggregator: Aggregator,
    start: Optional[int] = None,
    end: Optional[int] = None,
) -> List[Bucket]:
    """把 (timestamp, value) 点序列降采样到固定窗口。

    - points 可乱序、可有重复时间戳；同一批数据任意到达顺序结果完全一致。
    - start/end 缺省时取覆盖数据的最小窗口范围；显式给定时可超出数据范围，
      超出部分的窗口 value=None、count=0。
    - 返回按窗口起点升序的 Bucket 列表，覆盖 [start, end) 对齐后的所有窗口。
    """
    if not isinstance(window, int) or window <= 0:
        raise ValueError("window 必须为正整数")
    if start is not None and end is not None and end <= start:
        raise ValueError("end 必须大于 start")

    states: Dict[int, list] = {}
    for t, v in points:
        idx = t // window  # 地板除，负时间戳同样一致
        state = states.get(idx)
        if state is None:
            state = aggregator.new_state()
            states[idx] = state
        aggregator.add(state, v)

    if not states and (start is None or end is None):
        return []

    if start is None:
        start = min(states) * window
    if end is None:
        end = (max(states) + 1) * window

    first = start // window
    last = (end - 1) // window
    out: List[Bucket] = []
    for k in range(first, last + 1):
        state = states.get(k)
        if state is None:
            out.append(Bucket(k * window, (k + 1) * window, None, 0))
        else:
            out.append(
                Bucket(
                    k * window,
                    (k + 1) * window,
                    aggregator.finalize(state),
                    aggregator.count(state),
                )
            )
    return out


def naive_downsample(
    points: Iterable[Point],
    window: int,
    aggregator: Aggregator,
    start: int,
    end: int,
) -> List[Bucket]:
    """逐点朴素聚合参考实现：每个窗口独立扫描全部点。用于对拍。"""
    pts = list(points)
    first = start // window
    last = (end - 1) // window
    out: List[Bucket] = []
    for k in range(first, last + 1):
        lo, hi = k * window, (k + 1) * window
        vals = [v for t, v in pts if lo <= t < hi]
        out.append(
            Bucket(lo, hi, aggregator.finalize_values(vals), len(vals))
        )
    return out
