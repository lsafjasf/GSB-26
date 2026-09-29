"""规则覆盖度分析：命中统计、死规则检测、未覆盖分支清单、删除对拍。

与 rule_engine 的关系：
    rule_engine 在加载时做"单条规则完全覆盖"的硬校验（失败即拒绝加载）；
    本模块做离线分析，面向"规则表越写越多"的治理场景：
      1. 命中统计：每条规则在给定数据集上实际决定结果的次数；
      2. 死规则：条件自相矛盾，或被若干条更高优先级规则的**并集**完全遮蔽
         （比引擎的单条覆盖校验更强，可发现"多条规则合力遮蔽"的情形）；
      3. 未覆盖分支：数据集上命中 0 次的规则（不一定是死规则，只是没被
         当前数据触达，删除前需人工确认）；
      4. 删除对拍：删除死规则前后在同一数据集上逐例比对，证明行为不变。

可复算性：分析过程不含任何随机源与时间戳；报告文本完全由
（规则表, 数据集, 取值域）决定，同一输入必然逐字节一致。

集合型字段的取值域（domains）：证明"无约束字段被并集遮蔽"时需要知道
该字段的全部可能取值（如 tier 的枚举）。domains 缺省时，此类情形保守
处理为"不可判定"，不会误报死规则。
"""

from rule_engine import (
    Engine, RuleError, _is_empty, _match_value, _tighter_bound,
)


# ---------------------------------------------------------------- 区域运算
# 区域 = {字段: 规范化条件}，缺失字段表示无约束（匹配任意值）。

def _norm_intersect(a, b):
    """两个字段条件的交集（None 表示无约束）；结果可能为空条件。"""
    if a is None:
        return b
    if b is None:
        return a
    if a[0] == "set" and b[0] == "set":
        return ("set", a[1] & b[1])
    if a[0] == "range" and b[0] == "range":
        lo, lo_inc = _tighter_bound((a[1], a[2]), (b[1], b[2]), lower=True)
        hi, hi_inc = _tighter_bound((a[3], a[4]), (b[3], b[4]), lower=False)
        return ("range", lo, lo_inc, hi, hi_inc)
    st, rg = (a, b) if a[0] == "set" else (b, a)
    return ("set", frozenset(v for v in st[1] if _match_value(rg, v)))


def _norm_subtract(a, b, domain):
    """返回表示 a \\ b 的条件列表；无法精确表示时返回 None（保守）。"""
    if a is None:  # a 无约束
        if b[0] == "set":
            if domain is None:
                return None  # 不知道字段全集，无法表示"不在集合中"
            return [("set", frozenset(v for v in domain if v not in b[1]))]
        # b 是区间：补集为 (-inf, lo) ∪ (hi, +inf)
        _, lo, lo_inc, hi, hi_inc = b
        out = []
        if lo is not None:
            out.append(("range", None, True, lo, not lo_inc))
        if hi is not None:
            out.append(("range", hi, not hi_inc, None, True))
        return out
    if a[0] == "set":
        if b[0] == "set":
            return [("set", a[1] - b[1])]
        return [("set", frozenset(v for v in a[1] if not _match_value(b, v)))]
    # a 是区间
    if b[0] == "range":
        inter = _norm_intersect(a, b)
        if _is_empty(inter):
            return [a]
        out = []
        # 下侧 [a.lo, inter.lo)：仅当交集存在下界时才可能切出
        if inter[1] is not None:
            out.append(("range", a[1], a[2], inter[1], not inter[2]))
        # 上侧 (inter.hi, a.hi]：仅当交集存在上界时才可能切出
        if inter[3] is not None:
            out.append(("range", inter[3], not inter[4], a[3], a[4]))
        return [n for n in out if not _is_empty(n)]
    # a 区间、b 集合：逐个剔除落在区间内的点
    points = []
    for v in b[1]:
        try:
            if _match_value(a, v):
                points.append(v)
        except TypeError:
            continue  # 类型不可比（如字符串 vs 数值区间），视为不相交
    if not points:
        return [a]
    try:
        points = sorted(points)
    except TypeError:
        return None
    out = []
    lo, lo_inc = a[1], a[2]
    for p in points:
        seg = ("range", lo, lo_inc, p, False)
        if not _is_empty(seg):
            out.append(seg)
        lo, lo_inc = p, False
    tail = ("range", lo, lo_inc, a[3], a[4])
    if not _is_empty(tail):
        out.append(tail)
    return out


def _region_empty(region):
    return any(_is_empty(norm) for norm in region.values())


def _regions_overlap(a, b):
    if _region_empty(a) or _region_empty(b):
        return False
    for field in set(a) & set(b):
        if _is_empty(_norm_intersect(a[field], b[field])):
            return False
    return True


def _subtract_region(region, shield, domains):
    """返回 region \\ shield 的不交区域列表；无法精确表示时返回 None。"""
    out = []
    prefix = dict(region)
    for field in sorted(shield):
        sub = _norm_subtract(prefix.get(field), shield[field],
                             (domains or {}).get(field))
        if sub is None:
            return None
        for norm in sub:
            if _is_empty(norm):
                continue
            piece = dict(prefix)
            piece[field] = norm
            out.append(piece)
        inter = _norm_intersect(prefix.get(field), shield[field])
        if _is_empty(inter):
            break
        prefix[field] = inter
    return out


def _union_covers(shields, target, domains):
    """shields 的并集是否完全覆盖 target 区域。

    数学归纳：R ⊆ ∪S  ⟺  R \\ s₁ ⊆ ∪(S \\ {s₁})，递归拆分；
    遇到无法精确表示的补集时保守返回 False（不误报遮蔽）。
    """
    if _region_empty(target):
        return True
    if not shields:
        return False
    first, rest = shields[0], shields[1:]
    if not _regions_overlap(target, first):
        return _union_covers(rest, target, domains)
    pieces = _subtract_region(target, first, domains)
    if pieces is None:
        return False
    return all(_union_covers(rest, piece, domains) for piece in pieces)


# ---------------------------------------------------------------- 匹配器

def _sorted_concrete(rules):
    return sorted((r for r in rules if not r.is_default),
                  key=lambda r: -r.priority)


def decide(rules, order):
    """不触发 Engine 加载校验的匹配器，语义与 Engine.decide 一致。

    覆盖度分析需要能跑"含死规则的原始表"（Engine 会拒绝加载），
    因此这里复刻同一套优先级语义作为对拍基准。
    """
    for r in _sorted_concrete(rules):
        if r.matches(order):
            return r.result
    default = next((r for r in rules if r.is_default), None)
    if default is None:
        raise RuleError("缺少默认规则：默认分支必须显式声明（conditions=None）")
    return default.result


# ---------------------------------------------------------------- 分析

class CoverageReport:
    """一次覆盖度分析的全部结论（确定性，可复算）。"""

    def __init__(self, rules, dataset_name, domains):
        self.rules = rules
        self.dataset_name = dataset_name
        self.domains = domains or {}
        self.total = 0                 # 用例总数
        self.hits = {}                 # rid -> 命中次数（决定结果的次数）
        self.uncovered = []            # 命中 0 次的规则（含默认规则）
        self.dead = []                 # [(rule, 原因)]，永远不可能生效
        self.default_shadowed = False  # 默认规则被具体规则并集完全遮蔽
        self.diff_total = 0            # 删除死规则后对拍的用例数
        self.diff_mismatches = []      # 对拍不一致样例（应为空）


def infer_domains(cases):
    """从数据集推断集合型字段（取值全为 str/bool）的取值域。"""
    values = {}
    for case in cases:
        for field, v in case.items():
            values.setdefault(field, set()).add(v)
    return {field: sorted(vals, key=repr)
            for field, vals in sorted(values.items())
            if all(isinstance(v, (str, bool)) for v in vals)}


def find_dead_rules(rules, domains=None):
    """返回 (死规则列表, 默认规则是否被遮蔽)。

    死规则 = 条件自相矛盾，或被更高优先级规则的并集完全遮蔽。
    遮蔽检查只看严格更高优先级的规则（含自身已死的规则——它们照常
    参与匹配拦截），与引擎的匹配语义一致。
    """
    concrete = [r for r in rules if not r.is_default]
    dead = []
    for r in concrete:
        if r.is_contradictory():
            dead.append((r, "匹配条件自相矛盾"))
            continue
        higher = [h for h in concrete if h.priority > r.priority]
        if higher and _union_covers([h._norm for h in higher], r._norm,
                                    domains):
            dead.append((r, "被更高优先级规则的并集完全遮蔽"))
    defaults = [r for r in rules if r.is_default]
    default_shadowed = bool(defaults) and bool(concrete) and _union_covers(
        [h._norm for h in concrete], {}, domains)
    return dead, default_shadowed


def count_hits(rules, cases):
    """统计每条规则"决定结果"的次数；返回 ({rid: 次数}, 用例总数)。"""
    concrete = _sorted_concrete(rules)
    default = next((r for r in rules if r.is_default), None)
    hits = {r.rid: 0 for r in rules}
    total = 0
    for case in cases:
        total += 1
        fired = default
        for r in concrete:
            if r.matches(case):
                fired = r
                break
        if fired is not None:
            hits[fired.rid] += 1
    return hits, total


def strip_dead_rules(rules, dead):
    """删除死规则后的规则表（默认规则即使被遮蔽也保留——引擎要求兜底）。"""
    dead_ids = {id(r) for r, _ in dead}
    return [r for r in rules if id(r) not in dead_ids]


def analyze(rules, cases, domains=None, dataset_name=""):
    """完整分析：命中统计 + 死规则 + 未覆盖分支 + 删除对拍。"""
    rules = list(rules)
    for i, r in enumerate(rules, start=1):
        r.rid = i
    cases = list(cases)
    report = CoverageReport(rules, dataset_name, domains)

    report.hits, report.total = count_hits(rules, cases)
    report.uncovered = [r for r in rules if report.hits[r.rid] == 0]
    report.dead, report.default_shadowed = find_dead_rules(rules, domains)

    # 删除死规则后的行为对拍：原表（免校验匹配器） vs 清理表（Engine）
    cleaned = strip_dead_rules(rules, report.dead)
    engine = Engine(cleaned)  # 清理后的表必须能通过加载校验
    report.diff_total = len(cases)
    for case in cases:
        if decide(rules, case) != engine.decide(case):
            report.diff_mismatches.append(case)
    return report


# ---------------------------------------------------------------- 报告

def _pct(part, whole):
    return 100.0 * part / whole if whole else 0.0


def render_report(report):
    """渲染确定性文本报告：无时间戳、无随机源，同一输入逐字节一致。"""
    lines = []
    lines.append("规则覆盖度分析报告")
    lines.append("=" * 64)
    lines.append("数据集: %s（%d 例）" % (report.dataset_name, report.total))
    if report.domains:
        lines.append("集合型字段取值域（分析输入，来自数据集观测推断）:")
        for field, vals in report.domains.items():
            lines.append("  %s: %s" % (field, vals))
    lines.append("")

    lines.append("[一] 规则命中统计（命中 = 该规则决定了最终判定结果）")
    for r in report.rules:
        hits = report.hits[r.rid]
        lines.append("  规则 %2d (prio=%3d) %-22s -> %-13s 命中 %6d 次 (%5.2f%%)"
                     % (r.rid, r.priority, r.name or "-", r.result,
                        hits, _pct(hits, report.total)))
    lines.append("")

    lines.append("[二] 未被任何用例覆盖的分支: %d 条" % len(report.uncovered))
    if report.uncovered:
        for r in report.uncovered:
            lines.append("  %s -> %s（数据集中无一例由它决定结果；"
                         "不一定是死规则，删除前需人工确认）"
                         % (r.label(), r.result))
    else:
        lines.append("  （无）")
    lines.append("")

    lines.append("[三] 死规则（永远不可能生效）: %d 条" % len(report.dead))
    if report.dead:
        for r, reason in report.dead:
            lines.append("  %s -> %s：%s" % (r.label(), r.result, reason))
    else:
        lines.append("  （无）")
    lines.append("  默认规则: %s" % ("被具体规则的并集完全遮蔽，永不生效"
                                   if report.default_shadowed
                                   else "可达（存在只能走默认分支的输入）"))
    lines.append("")

    lines.append("[四] 删除死规则后的行为对拍")
    dead_ids = "、".join("规则 %d" % r.rid for r, _ in report.dead) or "无"
    lines.append("  删除对象: %s" % dead_ids)
    lines.append("  对拍用例: %d 例（原表免校验匹配器 vs 清理后 Engine）"
                 % report.diff_total)
    if report.diff_mismatches:
        lines.append("  结果: 不一致 %d 例！首个反例: %r"
                     % (len(report.diff_mismatches),
                        report.diff_mismatches[0]))
    else:
        lines.append("  结果: 逐例一致，删除死规则不改变任何判定。")
    return "\n".join(lines) + "\n"
