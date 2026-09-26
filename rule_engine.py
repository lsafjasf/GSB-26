"""规则表驱动的判定引擎（重构后的通用引擎，与具体业务无关）。

每条规则显式声明三要素：
    conditions  匹配条件（dict: 字段 -> 条件；None 表示默认规则）
    result      判定结果
    priority    优先级（数值，越大越优先；判定结果只由优先级决定，
                不依赖规则的声明顺序）

字段条件写法（同一字段的数值上下界可组合）：
    {"eq": v}                          等于
    {"in": [v1, v2, ...]}              属于集合
    {"gte": x} {"gt": x} {"lte": x} {"lt": x}   数值区间

加载（Engine 构造）时校验，失败抛出 RuleError 并指出规则序号：
    1. 默认规则必须存在且唯一；
    2. 冲突：两条规则条件重叠、优先级相同、结果不同；
    3. 不可达：规则条件自相矛盾，或被某条更高优先级规则完全覆盖。
"""

from itertools import combinations


class RuleError(Exception):
    """规则表加载校验失败。"""


# ---------------------------------------------------------------- 字段条件

def _norm_field(spec):
    """把单个字段条件规范化为 ('set', frozenset) 或
    ('range', lo, lo_inclusive, hi, hi_inclusive)，None 表示无界。"""
    if "eq" in spec:
        return ("set", frozenset([spec["eq"]]))
    if "in" in spec:
        return ("set", frozenset(spec["in"]))
    lo = hi = None
    lo_inc = hi_inc = True
    if "gte" in spec:
        lo, lo_inc = spec["gte"], True
    if "gt" in spec:
        lo, lo_inc = spec["gt"], False
    if "lte" in spec:
        hi, hi_inc = spec["lte"], True
    if "lt" in spec:
        hi, hi_inc = spec["lt"], False
    return ("range", lo, lo_inc, hi, hi_inc)


def _is_empty(norm):
    """单个字段条件是否自相矛盾（匹配不到任何值）。"""
    if norm[0] == "set":
        return not norm[1]
    _, lo, lo_inc, hi, hi_inc = norm
    if lo is None or hi is None:
        return False
    if lo > hi:
        return True
    if lo == hi:
        return not (lo_inc and hi_inc)
    return False


def _match_value(norm, value):
    if norm[0] == "set":
        return value in norm[1]
    _, lo, lo_inc, hi, hi_inc = norm
    if lo is not None and (value < lo or (value == lo and not lo_inc)):
        return False
    if hi is not None and (value > hi or (value == hi and not hi_inc)):
        return False
    return True


def _tighter_bound(x, y, lower):
    """取两个 (值, 是否含端点) 边界中更紧的一个；值 None 表示无界。"""
    if x[0] is None:
        return y
    if y[0] is None:
        return x
    if x[0] != y[0]:
        if lower:
            return x if x[0] > y[0] else y
        return x if x[0] < y[0] else y
    return (x[0], x[1] and y[1])


def _overlaps(a, b):
    """两个字段条件的交集是否非空。"""
    if a[0] == "set" and b[0] == "set":
        return bool(a[1] & b[1])
    if a[0] == "range" and b[0] == "range":
        lo, lo_inc = _tighter_bound((a[1], a[2]), (b[1], b[2]), lower=True)
        hi, hi_inc = _tighter_bound((a[3], a[4]), (b[3], b[4]), lower=False)
        return not _is_empty(("range", lo, lo_inc, hi, hi_inc))
    st, rg = (a, b) if a[0] == "set" else (b, a)
    return any(_match_value(rg, v) for v in st[1])


def _bound_covers(av, ai, bv, bi, lower):
    """边界 a 是否覆盖边界 b（b 一侧的取值都在 a 范围内）。"""
    if av is None:
        return True
    if bv is None:
        return False
    if av != bv:
        return av < bv if lower else av > bv
    return ai or not bi


def _covers(a, b):
    """字段条件 a 是否覆盖 b（凡 b 匹配的值 a 必匹配）。"""
    if a[0] == "set" and b[0] == "set":
        return b[1] <= a[1]
    if a[0] == "range" and b[0] == "range":
        return (_bound_covers(a[1], a[2], b[1], b[2], lower=True)
                and _bound_covers(a[3], a[4], b[3], b[4], lower=False))
    if a[0] == "range" and b[0] == "set":
        return all(_match_value(a, v) for v in b[1])
    # a 是有限集合、b 是区间：非空区间不可能被有限集合覆盖
    return False


# ---------------------------------------------------------------- 规则

class Rule:
    """一条业务规则。conditions 为 None 表示默认规则（兜底分支）。"""

    def __init__(self, conditions, result, priority, name=""):
        if conditions is None and priority != 0:
            raise RuleError("默认规则的优先级必须为 0")
        self.conditions = conditions
        self.result = result
        self.priority = priority
        self.name = name
        self.rid = None  # 规则序号，由 Engine 加载时按声明位置编号（从 1 开始）
        self._norm = None
        if conditions is not None:
            self._norm = {field: _norm_field(spec)
                          for field, spec in conditions.items()}

    @property
    def is_default(self):
        return self.conditions is None

    def label(self):
        return "规则 %d%s" % (self.rid, "（%s）" % self.name if self.name else "")

    def matches(self, order):
        return all(_match_value(norm, order[field])
                   for field, norm in self._norm.items())

    def is_contradictory(self):
        return any(_is_empty(norm) for norm in self._norm.values())

    def overlaps(self, other):
        for field in set(self._norm) & set(other._norm):
            if not _overlaps(self._norm[field], other._norm[field]):
                return False
        return True

    def covers(self, other):
        """本规则是否完全覆盖 other（other 匹配时本规则必匹配）。"""
        for field, norm in self._norm.items():
            if field not in other._norm:
                return False
            if not _covers(norm, other._norm[field]):
                return False
        return True


# ---------------------------------------------------------------- 引擎

def _validate(rules):
    errors = []

    defaults = [r for r in rules if r.is_default]
    if not defaults:
        errors.append("缺少默认规则：默认分支必须显式声明（conditions=None）")
    elif len(defaults) > 1:
        ids = "、".join("规则 %d" % r.rid for r in defaults)
        errors.append("默认规则不唯一：%s 都是默认规则" % ids)

    concrete = [r for r in rules if not r.is_default]

    for r in concrete:
        if r.is_contradictory():
            errors.append("%s 不可达：匹配条件自相矛盾" % r.label())

    for a, b in combinations(concrete, 2):
        if a.priority == b.priority and a.result != b.result and a.overlaps(b):
            errors.append(
                "%s 与 %s 冲突：优先级相同（%d）、条件重叠、"
                "结果不同（%r / %r）"
                % (a.label(), b.label(), a.priority, a.result, b.result))

    for r in concrete:
        if r.is_contradictory():
            continue
        for h in concrete:
            if h is not r and h.priority > r.priority and h.covers(r):
                errors.append("%s 不可达：被更高优先级的%s完全覆盖"
                              % (r.label(), h.label()))
                break

    if errors:
        raise RuleError("规则表校验失败：\n" + "\n".join(errors))


class Engine:
    """按优先级从高到低匹配，全部未命中时走默认规则。"""

    def __init__(self, rules):
        for i, r in enumerate(rules, start=1):
            r.rid = i
        _validate(rules)
        self._concrete = sorted((r for r in rules if not r.is_default),
                                key=lambda r: -r.priority)
        self._default = next(r for r in rules if r.is_default)

    def decide(self, order):
        for r in self._concrete:
            if r.matches(order):
                return r.result
        return self._default.result
