"""多键稳定排序库（仅依赖 Python 标准库）。

特性：
- 每个键可独立指定升/降序、空值位置（最前/最后）与自定义比较器。
- 排序稳定，且结果与「从末键开始多次稳定排序」完全一致。
- 比较器在排序前用随机/全量样本校验全序（自反性、反对称性、传递性），
  违反时抛出 TotalOrderError，而不是给出错乱结果。
- 混合类型键必须显式声明策略：reject（默认，报错）/ group（按类型分组）/
  coerce（统一转换），绝不静默按字符串比较。
"""

from functools import cmp_to_key
import itertools
import random

__all__ = [
    "KeySpec",
    "sort_multi",
    "sort_multi_reference",
    "TotalOrderError",
    "MixedTypeError",
]


class TotalOrderError(ValueError):
    """比较器违反全序（自反性/反对称性/传递性）时抛出。"""


class MixedTypeError(TypeError):
    """键值混合类型且策略为 reject 时抛出。"""


def _sign(x):
    return (x > 0) - (x < 0)


def _default_compare(a, b):
    return (a > b) - (a < b)


def _category(v):
    """把值归入可互相比较的类型类别。int/float 同属 number（可安全互比）。"""
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "str"
    if isinstance(v, bytes):
        return "bytes"
    return type(v).__name__


class KeySpec:
    """单个排序键的描述。

    key:        字典键名 / 对象属性名（字符串），或 callable(record) -> value。
    reverse:    True 表示降序（只影响值的比较方向，不影响空值位置）。
    nulls:      'first' 或 'last'，None 值排在最前或最后。
    comparator: 可选，cmp(a, b) -> 负/零/正，作用于非空原始值，必须满足全序。
    """

    __slots__ = ("key", "reverse", "nulls", "comparator")

    def __init__(self, key, *, reverse=False, nulls="last", comparator=None):
        if nulls not in ("first", "last"):
            raise ValueError("nulls 必须是 'first' 或 'last'")
        self.key = key
        self.reverse = bool(reverse)
        self.nulls = nulls
        self.comparator = comparator

    def getter(self):
        k = self.key
        if callable(k):
            return k

        def get(record, _k=k):
            if isinstance(record, dict):
                return record[_k]
            return getattr(record, _k)

        return get


class _Plan:
    __slots__ = ("getter", "reverse", "nulls_first", "comparator", "transform", "label")


def _normalize_keys(keys):
    out = []
    for k in keys:
        if isinstance(k, KeySpec):
            out.append(k)
        elif isinstance(k, dict):
            out.append(KeySpec(**k))
        else:
            out.append(KeySpec(k))
    return out


def _build_plans(specs, data, mixed, coerce_to):
    if mixed not in ("reject", "group", "coerce"):
        raise ValueError("mixed 必须是 'reject' / 'group' / 'coerce'")
    plans = []
    for spec in specs:
        plan = _Plan()
        plan.getter = spec.getter()
        plan.reverse = spec.reverse
        plan.nulls_first = spec.nulls == "first"
        plan.comparator = spec.comparator
        plan.label = spec.key if isinstance(spec.key, str) else getattr(
            spec.key, "__name__", repr(spec.key))
        plan.transform = None
        if spec.comparator is None:
            cats = set()
            for r in data:
                v = plan.getter(r)
                if v is not None:
                    cats.add(_category(v))
            if len(cats) > 1:
                if mixed == "reject":
                    raise MixedTypeError(
                        "键 %r 出现混合类型 %s；请显式指定 mixed='group' 或 "
                        "mixed='coerce'，不会静默按字符串比较"
                        % (plan.label, sorted(cats)))
                if mixed == "group":
                    rank = {name: i for i, name in enumerate(sorted(cats))}

                    def transform(v, _rank=rank):
                        return (_rank[_category(v)], v)

                    plan.transform = transform
                else:  # coerce
                    target = coerce_to if coerce_to is not None else str
                    if target is float:
                        plan.transform = float
                    elif target is str:
                        plan.transform = str
                    elif callable(target):
                        plan.transform = target
                    else:
                        raise ValueError("coerce_to 必须是 float / str / callable")
        plans.append(plan)
    return plans


def _validate_total_order(cmp, values, label, sample_size, rng):
    """用样本检测全序违反。样本量小（<=60）时全量枚举，否则随机抽样。"""
    n = len(values)
    if n == 0:
        return
    if n <= 60:
        pairs = itertools.product(values, repeat=2)
        triples = itertools.product(values, repeat=3)
    else:
        pairs = ((rng.choice(values), rng.choice(values))
                 for _ in range(sample_size))
        triples = ((rng.choice(values), rng.choice(values), rng.choice(values))
                   for _ in range(sample_size))
    for a, b in pairs:
        if cmp(a, a) != 0:
            raise TotalOrderError(
                "键 %r: 违反自反性 cmp(a,a)==0，cmp(%r, %r) = %r"
                % (label, a, a, cmp(a, a)))
        if _sign(cmp(a, b)) != -_sign(cmp(b, a)):
            raise TotalOrderError(
                "键 %r: 违反反对称性 sign(cmp(a,b))==-sign(cmp(b,a))，"
                "cmp(%r, %r) = %r 而 cmp(%r, %r) = %r"
                % (label, a, b, cmp(a, b), b, a, cmp(b, a)))
    for a, b, c in triples:
        if cmp(a, b) <= 0 and cmp(b, c) <= 0 and cmp(a, c) > 0:
            raise TotalOrderError(
                "键 %r: 违反传递性 a<=b 且 b<=c 但 a>c，a=%r b=%r c=%r"
                % (label, a, b, c))
        if cmp(c, b) <= 0 and cmp(b, a) <= 0 and cmp(c, a) > 0:
            raise TotalOrderError(
                "键 %r: 违反传递性 c<=b 且 b<=a 但 c>a，a=%r b=%r c=%r"
                % (label, a, b, c))


def _compare_values(plan, va, vb):
    """单键比较：先处理空值（不受 reverse 影响），再比值，最后应用方向。"""
    na, nb = va is None, vb is None
    if na or nb:
        if na and nb:
            return 0
        c = -1 if na else 1
        return c if plan.nulls_first else -c
    if plan.comparator is not None:
        c = plan.comparator(va, vb)
    else:
        t = plan.transform
        c = _default_compare(t(va) if t else va, t(vb) if t else vb)
    return -c if plan.reverse else c


def _composite_compare(plans):
    def cmp(ra, rb):
        for p in plans:
            c = _compare_values(p, p.getter(ra), p.getter(rb))
            if c:
                return c
        return 0

    return cmp


def _multipass_sort(data, plans):
    """无自定义比较器时的快路径：每键两次稳定 key 排序（值序 + 空值归位）。"""
    for p in reversed(plans):
        getter, transform = p.getter, p.transform

        def keyfn(r, g=getter, t=transform):
            v = g(r)
            if v is None:
                return (True, None)
            return (False, t(v) if t else v)

        data.sort(key=keyfn, reverse=p.reverse)
        if p.nulls_first:
            data.sort(key=lambda r, g=getter: g(r) is not None)
        else:
            data.sort(key=lambda r, g=getter: g(r) is None)


def sort_multi(records, keys, *, mixed="reject", coerce_to=None,
               validate=True, sample_size=200, seed=0):
    """按多个键稳定排序，返回新列表（不修改入参迭代器语义，输入被物化）。

    records:      可迭代的记录（dict / 对象）。
    keys:         KeySpec / dict / 键名 / callable 的列表，前者优先。
    mixed:        混合类型策略 'reject'（默认）/ 'group' / 'coerce'。
    coerce_to:    mixed='coerce' 时的目标类型，默认 str（全数值时无需转换）。
    validate:     排序前校验各键有效比较器满足全序。
    sample_size:  随机抽样校验的样本对/三元组数量（小数据集自动全量）。
    """
    specs = _normalize_keys(keys)
    data = list(records)
    plans = _build_plans(specs, data, mixed, coerce_to)
    if validate:
        rng = random.Random(seed)
        for spec, plan in zip(specs, plans):
            values = [v for v in (plan.getter(r) for r in data) if v is not None]
            if spec.comparator is not None:
                cmpf, vals = spec.comparator, values
            else:
                t = plan.transform
                cmpf = _default_compare
                vals = [t(v) if t else v for v in values]
            _validate_total_order(cmpf, vals, plan.label, sample_size, rng)
    if any(spec.comparator is not None for spec in specs):
        data.sort(key=cmp_to_key(_composite_compare(plans)))
    else:
        _multipass_sort(data, plans)
    return data


def sort_multi_reference(records, keys, *, mixed="reject", coerce_to=None):
    """参考实现：从最后一个键开始，逐键整体稳定排序。用于测试断言。"""
    specs = _normalize_keys(keys)
    data = list(records)
    plans = _build_plans(specs, data, mixed, coerce_to)
    for p in reversed(plans):
        data.sort(key=cmp_to_key(
            lambda a, b, p=p: _compare_values(p, p.getter(a), p.getter(b))))
    return data
