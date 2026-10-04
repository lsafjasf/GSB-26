"""分片并行查询执行引擎（仅标准库）。

两个执行入口，共享同一份分片扫描与合并代码：
  run_serial(sources, schema, plan)            串行：进程内依次扫描每个分片
  run_parallel(sources, schema, plan, workers) 并行：ProcessPoolExecutor

结果合并的不变式（与分片边界、完成顺序无关）：
  明细查询：每行带全局行号 idx；按 idx 归位、稳定排序，保证与
            "全表单遍扫描"逐行一致（逐值相等，含列顺序）。
  聚合查询：每分片产出部分聚合状态；按组键合流，聚合函数的合流
            口径见 README 的"聚合口径"一节。
"""

from __future__ import annotations

import math
from concurrent.futures import ProcessPoolExecutor

from . import expr as E
from .plan import Plan
from .table import read_csv_shard, shard_len


# ---------------------------------------------------------------- 聚合状态

class _AggState:
    """单个聚合度量的部分状态，可与同 func 的另一个状态合流。

    状态内容：
      count          : 行数（非 NULL 行；count(*) 为全部行）
      sum            : 求和（整数保持 int，浮点使用 float）
      min/max        : 最值（忽略 NULL；无输入时为 None）
      avg            : 内部保存 (sum, count)，最后相除
      count_distinct : set
      collect_distinct: set，最终输出排序后的列表（NULL 不参与）
    """

    __slots__ = ("func", "count", "total", "lo", "hi", "distinct")

    def __init__(self, func):
        self.func = func
        self.count = 0
        self.total = 0
        self.lo = None
        self.hi = None
        self.distinct = None
        if func in ("count_distinct", "collect_distinct"):
            self.distinct = set()

    def add(self, value):
        f = self.func
        if f == "count":
            if value is not None:
                self.count += 1
            return
        if value is None:
            return  # NULL 不参与 sum/min/max/avg/distinct
        if f == "sum":
            self.total += value
            self.count += 1
        elif f == "min":
            if self.lo is None or value < self.lo:
                self.lo = value
        elif f == "max":
            if self.hi is None or value > self.hi:
                self.hi = value
        elif f == "avg":
            self.total += value
            self.count += 1
        elif f == "count_distinct":
            self.distinct.add(value)
        elif f == "collect_distinct":
            self.distinct.add(value)

    def add_star(self):
        if self.func == "count":
            self.count += 1

    def merge(self, other):
        f = self.func
        if f == "count":
            self.count += other.count
        elif f == "sum":
            self.total += other.total
            self.count += other.count
        elif f == "avg":
            self.total += other.total
            self.count += other.count
        elif f == "min":
            if other.lo is not None and (self.lo is None or other.lo < self.lo):
                self.lo = other.lo
        elif f == "max":
            if other.hi is not None and (self.hi is None or other.hi > self.hi):
                self.hi = other.hi
        elif f in ("count_distinct", "collect_distinct"):
            self.distinct |= other.distinct

    def finish(self):
        f = self.func
        if f == "count":
            return self.count
        if f == "sum":
            return self.total
        if f == "avg":
            return (self.total / self.count) if self.count else None
        if f == "min":
            return self.lo
        if f == "max":
            return self.hi
        if f == "count_distinct":
            return len(self.distinct)
        if f == "collect_distinct":
            return sorted(self.distinct)


def _new_states(plan):
    return [_AggState(a.func) for a in plan.aggs]


# ---------------------------------------------------------------- 分片扫描

def _open_rows(source, schema):
    if isinstance(source, (list, tuple)):
        return list(source)
    return read_csv_shard(source, schema)


def scan_shard(source, schema, plan, base=0):
    """扫描单个分片，返回 (results, scanned)。

    聚合查询：results = [(组键tuple, [_AggState,...]), ...]，dict 形态
             {key: states}（调用方负责按序消费）。
    明细查询：results = [(全局行号, 输出tuple), ...]，保持片内顺序。
    base：本分片首行的全局行号。
    """
    rows = _open_rows(source, schema)
    scanned = len(rows)
    if plan.is_aggregate:
        groups = {}
        key_count = len(plan.group_by)
        if not key_count:
            # 标量聚合：空输入也要产生一行（count=0, sum=0, min/max/avg=NULL）
            groups[()] = _new_states(plan)
        for local_idx, row in enumerate(rows):
            if plan.filter is not None and not E.filter_match(plan.filter, row):
                continue
            if key_count:
                key = tuple(E.eval_expr(g, row) for g in plan.group_by)
            else:
                key = ()
            states = groups.get(key)
            if states is None:
                states = _new_states(plan)
                groups[key] = states
            for spec, st in zip(plan.aggs, states):
                if spec.expr is None:
                    st.add_star()
                else:
                    st.add(E.eval_expr(spec.expr, row))
        return groups, scanned

    out = []
    for local_idx, row in enumerate(rows):
        if plan.filter is not None and not E.filter_match(plan.filter, row):
            continue
        out.append((base + local_idx,
                    tuple(E.eval_expr(e, row) for _, e in plan.select)))
    if plan.order_by and plan.limit is not None:
        out = _local_topk(out, plan)
    return out, scanned


def _local_topk(indexed, plan):
    """ORDER BY + LIMIT 的本地 top-k 下推。

    全局 top-k 必然包含于各分片本地 top-k 的并集（排序键相同、并列时
    以全局行号兜底，分片内行号有序且排序稳定），因此分片内先裁到
    limit 行再合流，结果与全量合流后排序截断逐行一致。
    排序键含非列引用表达式时放弃下推（保守退回全量）。
    """
    aliases = [a for a, _ in plan.select]
    keys = []
    for ok in plan.order_by:
        e = ok.expr
        if e[0] != "col" or e[1] not in aliases:
            return indexed
        keys.append((aliases.index(e[1]), ok.desc))
    # indexed 已按全局行号升序；从末键到首键多趟稳定排序 => (keys, idx) 序
    for pos, desc in reversed(keys):
        indexed.sort(key=lambda t, p=pos: _asc_key(t[1][p]), reverse=desc)
    return indexed[:plan.limit]


# ---------------------------------------------------------------- 合流

def _merge_groups(partials, plan):
    """合流多个分片的部分聚合：{key: [states]} -> 有序 [(key, [states])]。

    组键按"第一次出现的顺序"合并；为保证串行/并行（完成顺序随机）结果
    一致，最终统一按组键确定性排序（见 _finish_aggregate）。
    """
    merged = {}
    for groups in partials:
        for key, states in groups.items():
            dst = merged.get(key)
            if dst is None:
                dst = _new_states(plan)
                merged[key] = dst
            for d, s in zip(dst, states):
                d.merge(s)
    return merged


# ---------------------------------------------------------------- 排序收尾

def _asc_key(v):
    # None 最小：升序排最前，降序时整体反转 -> 排最后
    return (v is not None, v)


def _order_tuples(rows, order_keys):
    """对输出 tuple 列表做多趟稳定排序（从末键到首键）。"""
    for ok in reversed(order_keys):
        rows.sort(key=lambda r, e=ok.expr: _asc_key(E.eval_expr(e, r)),
                  reverse=ok.desc)
    return rows


def _sort_key_tuple(key):
    return tuple(_asc_key(v) for v in key)


def _finish(merged_groups_or_rows, plan):
    """合流后统一收尾：最终化聚合值 / 排序 / limit。"""
    if plan.is_aggregate:
        groups = merged_groups_or_rows
        out_cols = plan.output_columns()
        rows = []
        for key, states in groups.items():
            vals = list(key) + [st.finish() for st in states]
            rows.append(dict(zip(out_cols, vals)))
        if plan.order_by:
            _order_tuples(rows, plan.order_by)
        else:
            # 无显式排序：按组键确定性排序（含 None，None 最小）
            rows.sort(key=lambda r: _sort_key_tuple(tuple(
                r[f"key_{i}"] for i in range(len(plan.group_by)))))
    else:
        indexed = merged_groups_or_rows
        # 全局行号归位：与完成顺序无关，稳定 => 与单遍扫描逐行一致
        indexed.sort(key=lambda t: t[0])
        cols = plan.output_columns()
        rows = [dict(zip(cols, vals)) for _, vals in indexed]
        if plan.order_by:
            _order_tuples(rows, plan.order_by)
    if plan.limit is not None:
        rows = rows[:plan.limit]
    return rows


# ---------------------------------------------------------------- 执行入口

def run_serial(sources, schema, plan):
    """串行基线：进程内按分片顺序扫描（顺序与"一张整表"相同）。"""
    if not sources:
        raise ValueError("no shards")
    if plan.is_aggregate:
        partials = []
        for src in sources:
            groups, _ = scan_shard(src, schema, plan, 0)
            partials.append(groups)
        merged = _merge_groups(partials, plan)
        return _finish(merged, plan)
    base = 0
    indexed = []
    for src in sources:
        rows, n = scan_shard(src, schema, plan, base)
        indexed.extend(rows)
        base += n
    return _finish(indexed, plan)


# 顶层函数：worker 进程入口（必须可 pickle，在模块顶层）
def _worker_scan(item):
    pos, source, schema, plan, base = item
    groups_or_rows, scanned = scan_shard(source, schema, plan, base)
    return pos, groups_or_rows, scanned


def run_parallel(sources, schema, plan, workers=2):
    """并行执行：workers=1 退化为进程内串行（但仍走分片+合流路径）。"""
    if not sources:
        raise ValueError("no shards")
    if workers is None or workers <= 0:
        workers = 1

    # 先串行算出每个分片的全局行号偏移（仅数行，成本可忽略）
    bases, b = [], 0
    for src in sources:
        bases.append(b)
        b += shard_len(src)

    tasks = [(i, src, schema, plan, bases[i]) for i, src in enumerate(sources)]
    if workers == 1:
        results = [_worker_scan(t) for t in tasks]
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(_worker_scan, tasks))

    results.sort(key=lambda r: r[0])   # 按分片号归位（map 本就有序，显式保底）
    if plan.is_aggregate:
        return _finish(_merge_groups([g for _, g, _ in results], plan), plan)
    indexed = []
    for _, rows, _ in results:
        indexed.extend(rows)
    return _finish(indexed, plan)


def run(sources, schema, plan, workers=1):
    """统一入口：workers=1 串行，>1 多进程并行。"""
    if workers == 1:
        return run_serial(sources, schema, plan)
    return run_parallel(sources, schema, plan, workers)


def row_signature(rows):
    """结果签名：供对拍使用（repr 规范化，含 None/float/排序结构）。"""
    return [tuple((k, _fmt(v)) for k, v in r.items()) for r in rows for _fmt in [_sig_fmt]]


def _sig_fmt(v):
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, list):
        return tuple(_sig_fmt(x) for x in v)
    return v


def assert_rows_equal(a, b, float_rel=1e-12, float_abs=1e-12):
    """严格逐行对拍；默认要求逐值相等，float 用 isclose 放宽（见 README）。"""
    sig_a, sig_b = row_signature(a), row_signature(b)
    if sig_a == sig_b:
        return True
    # 签名不同：逐值比较，仅允许 float 容差差异
    if len(a) != len(b):
        raise AssertionError(f"row count differs: {len(a)} vs {len(b)}")
    for i, (ra, rb) in enumerate(zip(a, b)):
        if list(ra) != list(rb):
            raise AssertionError(f"row {i} columns differ: {ra} vs {rb}")
        for k in ra:
            x, y = ra[k], rb[k]
            if isinstance(x, float) or isinstance(y, float):
                if not math.isclose(float(x), float(y),
                                    rel_tol=float_rel, abs_tol=float_abs):
                    raise AssertionError(f"row {i} col {k}: {x!r} vs {y!r}")
            elif x != y:
                raise AssertionError(f"row {i} col {k}: {x!r} vs {y!r}")
    return True
