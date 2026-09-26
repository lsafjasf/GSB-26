"""多键稳定排序库（仅标准库）。

特性：
- 每个键可独立指定升/降序、空值位置（最前/最后）与自定义比较器。
- 排序稳定，单趟比较器结果与「多次稳定排序」一致。
- 排序前对比较器做全序校验（自反性、反对称性、传递性），违反即报错。
- 混合类型键必须显式声明策略：reject / group / coerce，绝不静默按字符串比较。
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from functools import cmp_to_key
from typing import Any, Callable, Iterable, List, Optional, Sequence, Union

__all__ = [
    "KeySpec",
    "sort_multi",
    "make_comparator",
    "validate_total_order",
    "MultiSortError",
    "MixedTypeError",
    "TotalOrderError",
]

KeyLike = Union[str, int, Callable[[Any], Any]]


class MultiSortError(Exception):
    """本库所有异常的基类。"""


class MixedTypeError(MultiSortError):
    """键值出现混合类型且策略不允许，或类型不可排序。"""


class TotalOrderError(MultiSortError):
    """比较器违反全序（自反性 / 反对称性 / 传递性）。"""


def _sign(x: int) -> int:
    return (x > 0) - (x < 0)


def _type_group(value: Any) -> int:
    """类型分组：0=数值（含 bool），1=字符串，2=其他。"""
    if isinstance(value, (int, float)):  # bool 是 int 子类，归入数值
        return 0
    if isinstance(value, str):
        return 1
    return 2


def _natural(a: Any, b: Any) -> int:
    try:
        return (a > b) - (a < b)
    except TypeError as exc:
        raise MixedTypeError(
            f"键值类型不可比较: {type(a).__name__} 与 {type(b).__name__}"
        ) from exc


@dataclass(frozen=True)
class KeySpec:
    """单个排序键的声明。

    key:     记录取值器——属性/字典名（str）、下标（int）或 callable。
    reverse: True 表示降序。
    nulls:   'first' 或 'last'，控制 None 的位置（与 reverse 无关）。
    cmp:     自定义比较器 cmp(a, b) -> 负/零/正；为 None 时用内置比较。
    mixed:   混合类型策略：'reject'（报错）/ 'group'（按类型分组）/ 'coerce'（统一转换）。
    coerce:  mixed='coerce' 时必填，把任意键值转换为统一类型。
    """

    key: KeyLike
    reverse: bool = False
    nulls: str = "last"
    cmp: Optional[Callable[[Any, Any], int]] = None
    mixed: str = "reject"
    coerce: Optional[Callable[[Any], Any]] = None

    def __post_init__(self) -> None:
        if self.nulls not in ("first", "last"):
            raise ValueError(f"nulls 必须是 'first' 或 'last'，得到 {self.nulls!r}")
        if self.mixed not in ("reject", "group", "coerce"):
            raise ValueError(f"mixed 必须是 'reject'/'group'/'coerce'，得到 {self.mixed!r}")
        if self.mixed == "coerce" and self.coerce is None:
            raise ValueError("mixed='coerce' 时必须提供 coerce 转换函数")


def _make_extractor(key: KeyLike) -> Callable[[Any], Any]:
    if callable(key):
        return key
    if isinstance(key, int):
        def by_index(rec: Any) -> Any:
            return rec[key]
        return by_index

    def by_name(rec: Any) -> Any:
        if isinstance(rec, dict):
            return rec[key]
        return getattr(rec, key)

    return by_name


def _compare_basic(a: Any, b: Any, spec: KeySpec) -> int:
    """内置比较（无自定义 cmp 时），按 mixed 策略处理混合类型。"""
    ga, gb = _type_group(a), _type_group(b)
    if ga == gb:
        return _natural(a, b)
    if spec.mixed == "reject":
        raise MixedTypeError(
            f"键 {spec.key!r} 出现混合类型 {type(a).__name__} 与 {type(b).__name__}；"
            "请显式指定 mixed='group' 或 mixed='coerce'"
        )
    if spec.mixed == "coerce":
        ca, cb = spec.coerce(a), spec.coerce(b)
        if _type_group(ca) != _type_group(cb):
            raise MixedTypeError(
                f"coerce 后类型仍不一致: {type(ca).__name__} 与 {type(cb).__name__}"
            )
        return _natural(ca, cb)
    # mixed == 'group'：先按类型组排序（数值 < 字符串 < 其他）
    return _sign(ga - gb)


def make_comparator(keys: Sequence[Union[KeySpec, KeyLike]]) -> Callable[[Any, Any], int]:
    """根据键声明生成记录级比较器（单趟、稳定语义）。"""
    specs = [k if isinstance(k, KeySpec) else KeySpec(k) for k in keys]
    extractors = [_make_extractor(s.key) for s in specs]

    def cmp_records(ra: Any, rb: Any) -> int:
        for spec, extract in zip(specs, extractors):
            va, vb = extract(ra), extract(rb)
            a_null, b_null = va is None, vb is None
            if a_null or b_null:
                if a_null and b_null:
                    continue
                c = -1 if a_null else 1
                if spec.nulls == "last":
                    c = -c
                return c  # 空值位置不受 reverse 影响
            if spec.cmp is not None:
                c = _sign(spec.cmp(va, vb))
            else:
                c = _compare_basic(va, vb, spec)
            if c:
                return -c if spec.reverse else c
        return 0

    return cmp_records


def validate_total_order(
    cmp: Callable[[Any, Any], int],
    items: Sequence[Any],
    sample_size: int = 256,
    triple_checks: int = 100_000,
    seed: int = 0,
) -> None:
    """在随机样本上校验比较器是否满足全序，违反时抛出 TotalOrderError。

    检查：自反性 cmp(x,x)==0；反对称性 sign(cmp(x,y))==-sign(cmp(y,x))；
    传递性 cmp(x,y)<=0 且 cmp(y,z)<=0 蕴含 cmp(x,z)<=0。
    样本量 <= 60 时传递性做全量三元组检查，否则随机抽样 triple_checks 组。
    """
    rng = random.Random(seed)
    n = len(items)
    sample = list(items) if n <= sample_size else rng.sample(list(items), sample_size)
    m = len(sample)

    for x in sample:
        if cmp(x, x) != 0:
            raise TotalOrderError(f"违反自反性: cmp(x, x) != 0，x = {x!r}")

    for i in range(m):
        for j in range(i + 1, m):
            x, y = sample[i], sample[j]
            if _sign(cmp(x, y)) != -_sign(cmp(y, x)):
                raise TotalOrderError(
                    f"违反反对称性: cmp(x,y) 与 cmp(y,x) 符号不相反，x = {x!r}, y = {y!r}"
                )

    def check_triple(x: Any, y: Any, z: Any) -> None:
        if cmp(x, y) <= 0 and cmp(y, z) <= 0 and cmp(x, z) > 0:
            raise TotalOrderError(
                f"违反传递性: x<=y 且 y<=z 但 x>z，x = {x!r}, y = {y!r}, z = {z!r}"
            )

    if m <= 60:
        for x in sample:
            for y in sample:
                for z in sample:
                    check_triple(x, y, z)
    else:
        for _ in range(triple_checks):
            check_triple(rng.choice(sample), rng.choice(sample), rng.choice(sample))


def _make_keyfn(spec: KeySpec, extract: Callable[[Any], Any]) -> Callable[[Any], Any]:
    """为多次稳定排序构造单键 key 函数（快路径，仅用于无自定义 cmp 的键）。"""
    if spec.mixed == "reject":
        def keyfn(rec: Any) -> Any:
            return extract(rec)
        return keyfn
    if spec.mixed == "group":
        def keyfn(rec: Any) -> Any:
            v = extract(rec)
            g = _type_group(v)
            if g == 2:
                raise MixedTypeError(
                    f"键 {spec.key!r} 含不可排序类型 {type(v).__name__}，请使用 mixed='coerce'"
                )
            return (g, v)
        return keyfn
    # coerce
    def keyfn(rec: Any) -> Any:
        return spec.coerce(extract(rec))
    return keyfn


def _multipass_sort(items: List[Any], specs: Sequence[KeySpec]) -> List[Any]:
    """多次稳定排序：从最后一个键到第一个键逐趟排序（利用 Python sort 的稳定性）。"""
    result = list(items)
    for spec in reversed(specs):
        extract = _make_extractor(spec.key)
        nulls_part = [r for r in result if extract(r) is None]
        non_null = [r for r in result if extract(r) is not None]
        keyfn = _make_keyfn(spec, extract)
        try:
            non_null.sort(key=keyfn, reverse=spec.reverse)
        except TypeError as exc:
            raise MixedTypeError(
                f"键 {spec.key!r} 出现混合/不可比较类型；"
                "请显式指定 mixed='group' 或 mixed='coerce'"
            ) from exc
        result = nulls_part + non_null if spec.nulls == "first" else non_null + nulls_part
    return result


def sort_multi(
    records: Iterable[Any],
    keys: Sequence[Union[KeySpec, KeyLike]],
    *,
    validate: bool = True,
    sample_size: int = 256,
) -> List[Any]:
    """按多个键稳定排序，返回新列表（不修改输入）。

    - 空值（None）位置由每个键的 nulls 独立控制，且不受 reverse 影响。
    - validate=True 时先对比较器做全序校验（随机样本），违反即抛 TotalOrderError。
    - 无自定义比较器时走多次稳定排序快路径；有自定义比较器时走单趟 cmp_to_key。
      两条路径结果一致（测试中有断言）。
    """
    specs = [k if isinstance(k, KeySpec) else KeySpec(k) for k in keys]
    items = list(records)
    if not items or not specs:
        return items

    cmp_records = make_comparator(specs)
    if validate:
        validate_total_order(cmp_records, items, sample_size=sample_size)

    if all(s.cmp is None for s in specs):
        return _multipass_sort(items, specs)
    return sorted(items, key=cmp_to_key(cmp_records))
