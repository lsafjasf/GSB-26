"""时序指标降采样库（仅标准库，时间用整数表示）。

核心口径（详细说明见 README.md）：
- 窗口为半开区间 [k*window, (k*window)+window)，按 epoch 对齐；
  落在边界上的点属于右侧窗口（floor 划分，负时间戳同样适用）。
- 聚合器分两类：可加（count/sum）与不可加（max/avg/quantile），
  同一次降采样请求中混用两类会抛 MixedAggregationError。
- 无采样点的窗口值为 None（空洞），绝不静默补零。
- 重复时间戳视为独立采样点，全部计入；输入顺序不影响结果。
- sum/avg 的数值累加使用 math.fsum 精确求和（正确舍入），
  而非逐项浮点累加：同一组值无论以何种到达顺序喂入，
  得到的都是同一个正确舍入结果，浮点误差不会随输入顺序漂移。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

Point = Tuple[int, float]

__all__ = [
    "MetricKind",
    "Aggregator",
    "Bucket",
    "AGGREGATORS",
    "quantile",
    "register_aggregator",
    "downsample",
    "naive_downsample",
    "DownsampleError",
    "MixedAggregationError",
    "UnknownAggregatorError",
    "InvalidWindowError",
    "InvalidPointError",
]


class MetricKind(Enum):
    """指标聚合口径类别。"""

    ADDITIVE = "additive"          # 可加指标：计数、求和
    NON_ADDITIVE = "non_additive"  # 不可加指标：最大值、平均值、分位数


class DownsampleError(Exception):
    """降采样相关错误基类。"""


class MixedAggregationError(DownsampleError):
    """同一次请求混用了可加与不可加聚合口径。"""


class UnknownAggregatorError(DownsampleError):
    """请求了未注册的聚合器。"""


class InvalidWindowError(DownsampleError):
    """降采样窗口非法（非正整数）。"""


class InvalidPointError(DownsampleError):
    """采样点非法（时间戳非整数等）。"""


@dataclass(frozen=True)
class Aggregator:
    """聚合器定义：名称 + 口径类别 + 对一组采样值的聚合函数。"""

    name: str
    kind: MetricKind
    func: Callable[[Sequence[float]], float]


def _quantile_func(p: float) -> Callable[[Sequence[float]], float]:
    if not 0.0 <= p <= 1.0:
        raise ValueError(f"quantile p 必须在 [0, 1] 内，得到 {p!r}")

    def q(values: Sequence[float]) -> float:
        # 线性插值分位数（与 numpy 默认 'linear' 方法一致）
        s = sorted(values)
        n = len(s)
        if n == 1:
            return float(s[0])
        rank = p * (n - 1)
        lo = math.floor(rank)
        hi = math.ceil(rank)
        if lo == hi:
            return float(s[lo])
        frac = rank - lo
        return s[lo] * (1.0 - frac) + s[hi] * frac

    return q


def _stable_sum(values: Sequence[float]) -> float:
    """精确求和：返回值只取决于输入的值集合，与顺序逐位一致。

    使用 math.fsum（Shewchuk 部分和算法），返回正确舍入的浮点和，
    避免逐项累加/补偿求和在大动态范围数据下随顺序漂移的误差。
    +inf 与 -inf 混合时数学和未定义，fsum 会抛 ValueError，
    此时回退为 NaN（同样与顺序无关）。
    """
    try:
        return math.fsum(values)
    except ValueError:
        return float("nan")


def quantile(p: float) -> Aggregator:
    """构造一个分位数聚合器（不可加口径）。"""
    return Aggregator(f"p{int(round(p * 100))}", MetricKind.NON_ADDITIVE, _quantile_func(p))


AGGREGATORS: Dict[str, Aggregator] = {
    "count": Aggregator("count", MetricKind.ADDITIVE, lambda vs: float(len(vs))),
    "sum": Aggregator("sum", MetricKind.ADDITIVE, _stable_sum),
    "max": Aggregator("max", MetricKind.NON_ADDITIVE, lambda vs: float(max(vs))),
    "avg": Aggregator(
        "avg",
        MetricKind.NON_ADDITIVE,
        lambda vs: _stable_sum(vs) / len(vs),
    ),
}


def register_aggregator(agg: Aggregator) -> None:
    """注册自定义聚合器（必须声明口径类别）。"""
    if not isinstance(agg.kind, MetricKind):
        raise DownsampleError(f"聚合器 {agg.name!r} 必须声明 MetricKind")
    AGGREGATORS[agg.name] = agg


for _p in (0.5, 0.9, 0.95, 0.99):
    register_aggregator(quantile(_p))


@dataclass(frozen=True)
class Bucket:
    """一个降采样窗口的结果。

    values 为 None 表示该窗口内没有任何采样点（空洞），
    否则为 {聚合器名: 聚合值}。
    """

    start: int
    end: int
    values: Optional[Dict[str, float]]

    @property
    def empty(self) -> bool:
        return self.values is None


def _validate_window(window: int) -> None:
    if isinstance(window, bool) or not isinstance(window, int) or window <= 0:
        raise InvalidWindowError(f"降采样窗口必须是正整数，得到 {window!r}")


def _resolve_aggregators(aggs) -> List[Aggregator]:
    if isinstance(aggs, (str, Aggregator)):
        aggs = [aggs]
    resolved: List[Aggregator] = []
    for a in aggs:
        if isinstance(a, str):
            if a not in AGGREGATORS:
                raise UnknownAggregatorError(f"未知聚合器: {a!r}")
            resolved.append(AGGREGATORS[a])
        elif isinstance(a, Aggregator):
            resolved.append(a)
        else:
            raise UnknownAggregatorError(f"未知聚合器: {a!r}")
    if not resolved:
        raise DownsampleError("至少需要一个聚合器")
    kinds = {a.kind for a in resolved}
    if len(kinds) > 1:
        names = [a.name for a in resolved]
        raise MixedAggregationError(
            f"可加与不可加聚合口径不能混用于同一次降采样: {names}"
        )
    return resolved


def _validate_points(points: Iterable[Point]) -> List[Point]:
    pts = list(points)
    for t, _v in pts:
        if isinstance(t, bool) or not isinstance(t, int):
            raise InvalidPointError(f"时间戳必须是整数，得到 {t!r}")
    return pts


def _window_span(pts: List[Point], window: int,
                 start: Optional[int], end: Optional[int]) -> Optional[Tuple[int, int]]:
    """计算输出窗口下标范围 [k0, k1]（闭区间）。无输出时返回 None。"""
    if start is not None and end is not None:
        if end <= start:
            raise DownsampleError(f"时间范围非法: start={start!r} end={end!r}")
        return start // window, (end - 1) // window
    if start is not None or end is not None:
        raise DownsampleError("start 与 end 必须同时提供或同时省略")
    if not pts:
        return None
    lo = min(t for t, _ in pts)
    hi = max(t for t, _ in pts)
    return lo // window, hi // window


def downsample(points: Iterable[Point],
               window: int,
               aggs,
               start: Optional[int] = None,
               end: Optional[int] = None) -> List[Bucket]:
    """把细粒度采样点降采样到 window 对齐的窗口。

    points: (整数时间戳, 数值) 可迭代对象，允许乱序与重复时间戳。
    window: 正整数窗口宽度。
    aggs:   聚合器名/对象或它们的列表；同一次请求口径类别必须一致。
    start/end: 可选的输出时间范围 [start, end)；省略时取数据本身的范围。
               范围超出数据时，超出的窗口为空洞（values=None）。
    """
    _validate_window(window)
    resolved = _resolve_aggregators(aggs)
    pts = _validate_points(points)

    span = _window_span(pts, window, start, end)
    if span is None:
        return []
    k0, k1 = span

    # 一趟分桶：窗口下标 -> 采样值列表。与到达顺序无关。
    buckets: Dict[int, List[float]] = {}
    for t, v in pts:
        k = t // window
        if k0 <= k <= k1:
            buckets.setdefault(k, []).append(v)

    out: List[Bucket] = []
    for k in range(k0, k1 + 1):
        values = buckets.get(k)
        if values is None:
            out.append(Bucket(k * window, (k + 1) * window, None))
        else:
            out.append(Bucket(
                k * window,
                (k + 1) * window,
                {a.name: a.func(values) for a in resolved},
            ))
    return out


def naive_downsample(points: Iterable[Point],
                     window: int,
                     aggs,
                     start: Optional[int] = None,
                     end: Optional[int] = None) -> List[Bucket]:
    """逐点朴素聚合的参照实现：每个窗口独立地全量扫描所有点。

    用于与 downsample 对拍，验证分桶实现的正确性。
    """
    _validate_window(window)
    resolved = _resolve_aggregators(aggs)
    pts = _validate_points(points)

    span = _window_span(pts, window, start, end)
    if span is None:
        return []
    k0, k1 = span

    out: List[Bucket] = []
    for k in range(k0, k1 + 1):
        lo, hi = k * window, (k + 1) * window
        values = [v for t, v in pts if lo <= t < hi]
        if not values:
            out.append(Bucket(lo, hi, None))
        else:
            out.append(Bucket(lo, hi, {a.name: a.func(values) for a in resolved}))
    return out
