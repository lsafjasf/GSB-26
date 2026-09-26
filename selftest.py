"""自测套件：python3 selftest.py

覆盖：空图、无调用、深链、自递归、相互递归、多环交织、不确定边保留、
环路径验证、增量更新一致性（含随机多轮）、源码级增量更新。
仅使用标准库。
"""

import random
import sys

from callgraph import (
    CallGraph,
    IncrementalEngine,
    find_cycles,
    format_report,
    verify_cycle,
)
from callgraph.parser import parse_module

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def paths(cycles):
    return sorted(c.path for c in cycles)


# ---------------------------------------------------------------- 基本场景
def test_empty_graph():
    print("== 空图 ==")
    g = CallGraph()
    cycles = find_cycles(g)
    check("空图无环", cycles == [])
    check("空图统计为零", g.stats()["edges"] == 0)


def test_no_calls():
    print("== 无调用 ==")
    src = "def a():\n    pass\n\ndef b():\n    x = 1\n    return x\n"
    g = CallGraph.from_source(src)
    cycles = find_cycles(g)
    check("无调用则无环", cycles == [])
    check("函数节点都在", set(g.internal_nodes()) == {"a", "b"})


def test_deep_chain():
    print("== 深链（5000 层无环）==")
    g = CallGraph()
    n = 5000
    for i in range(n - 1):
        g.add_call(f"f{i}", f"f{i+1}", True, "direct")
    g.add_call(f"f{n-1}", "<external:print>", True, "external")
    cycles = find_cycles(g)
    check("深链无环", cycles == [])
    check("深链边数正确", g.num_edges() == n)


def test_self_recursion():
    print("== 自递归 ==")
    src = "def fact(n):\n    return 1 if n <= 1 else n * fact(n - 1)\n"
    g = CallGraph.from_source(src)
    cycles = find_cycles(g)
    check("检测到一个环", len(cycles) == 1)
    c = cycles[0]
    check("分类为直接递归", c.kind == "self-recursion")
    check("长度为 1", c.length == 1)
    check("环路径可验证", verify_cycle(g, c))


def test_mutual_recursion():
    print("== 相互递归 ==")
    src = (
        "def is_even(n):\n    return True if n == 0 else is_odd(n - 1)\n"
        "def is_odd(n):\n    return False if n == 0 else is_even(n - 1)\n"
    )
    g = CallGraph.from_source(src)
    cycles = find_cycles(g)
    check("检测到一个环", len(cycles) == 1)
    c = cycles[0]
    check("分类为相互递归", c.kind == "mutual-recursion")
    check("长度为 2", c.length == 2)
    check("涉及函数正确", c.functions == {"is_even", "is_odd"})
    check("环路径可验证", verify_cycle(g, c))


def test_interleaved_cycles():
    print("== 多环交织 ==")
    # a->b, b->a, b->c, c->b, c->a, d->d, e->f, f->e, f->g, g->e
    g = CallGraph()
    for u, v in [("a", "b"), ("b", "a"), ("b", "c"), ("c", "b"), ("c", "a"),
                 ("d", "d"), ("e", "f"), ("f", "e"), ("f", "g"), ("g", "e")]:
        g.add_call(u, v, True, "direct")
    cycles = find_cycles(g)
    got = paths(cycles)
    expect = sorted([("a", "b"), ("a", "b", "c"), ("b", "c"), ("d",),
                     ("e", "f"), ("e", "f", "g")])
    check("枚举出全部 6 个基本环", got == expect, f"got={got}")
    check("全部环路径可验证", all(verify_cycle(g, c) for c in cycles))
    kinds = {}
    for c in cycles:
        kinds[c.kind] = kinds.get(c.kind, 0) + 1
    check("自环/相互递归分类正确", kinds == {"self-recursion": 1, "mutual-recursion": 5})


# ---------------------------------------------------------------- 不确定边
def test_uncertain_edges_kept():
    print("== 不确定调用标注与保留 ==")
    src = """
import os
def runner(cb):
    cb()
def dispatch(obj):
    obj.m()
def use_getattr(o):
    getattr(o, "m")()
def use_subscript(table):
    table["k"]()
def calls_external():
    os.getcwd()
    len([1])
class A:
    def m(self):
        pass
"""
    model = parse_module(src)
    g = CallGraph.from_model(model)
    st = g.stats()
    check("存在不确定边", st["uncertain_edges"] > 0, str(st))
    # 参数调用 -> 合成未知节点
    unknowns = [t for (u, t) in g.edge_info if t.startswith("<unknown:")]
    check("参数/下标/getattr 调用保留为未知节点边", len(unknowns) >= 3, str(unknowns))
    # 动态分派 -> 候选方法边（不确定）
    check("动态分派产生到 A.m 的不确定候选边",
          g.has_edge("dispatch", "A.m") and not g.edge_certain("dispatch", "A.m"))
    check("getattr 也产生候选边", g.has_edge("use_getattr", "A.m"))
    # 外部调用 -> 外部节点（确定）
    check("外部调用指向外部节点", g.has_edge("calls_external", "<external:os.getcwd>"))
    check("外部边为确定边", g.edge_certain("calls_external", "<external:os.getcwd>"))


def test_potential_cycle_via_dynamic_dispatch():
    print("== 动态分派形成的潜在环 ==")
    src = """
class Worker:
    def run(self):
        self.step()
    def step(self):
        kick(self)
def kick(obj):
    obj.run()
"""
    g = CallGraph.from_source(src)
    cycles = find_cycles(g)
    check("检测到潜在环", len(cycles) == 1, str(paths(cycles)))
    c = cycles[0]
    check("路径为 Worker.run -> Worker.step -> kick",
          c.path == ("Worker.run", "Worker.step", "kick"), str(c.path))
    check("置信度为 potential", c.confidence == "potential")
    check("潜在环路径同样可验证", verify_cycle(g, c))


def test_cycle_path_verification():
    print("== 环路径验证（正例 + 反例）==")
    src = (
        "def a():\n    b()\n"
        "def b():\n    c()\n"
        "def c():\n    a()\n"
    )
    g = CallGraph.from_source(src)
    cycles = find_cycles(g)
    check("检测到一个三环", len(cycles) == 1 and cycles[0].length == 3)
    check("真实环验证通过", verify_cycle(g, cycles[0]))
    from callgraph.cycles import Cycle
    fake = Cycle(path=("a", "c"), kind="mutual-recursion",
                 confidence="certain", edges=(("a", "c"), ("c", "a")))
    check("捏造的环（a->c 边不存在）验证失败", not verify_cycle(g, fake))


# ---------------------------------------------------------------- 增量更新
def test_incremental_add_and_break_cycle():
    print("== 增量更新：成环与破环 ==")
    src = (
        "def a():\n    b()\n"
        "def b():\n    pass\n"
        "def c():\n    a()\n"
    )
    model = parse_module(src)
    g = CallGraph.from_model(model)
    eng = IncrementalEngine(g, model)
    check("初始无环", eng.cycles == [])

    rep = eng.update_function_source("b", "def b():\n    c()")
    check("增量更新后出现环 a->b->c",
          paths(eng.cycles) == [("a", "b", "c")], str(paths(eng.cycles)))
    check("受影响子图只含 3 个节点", rep.region_size == 3, str(rep.region_size))
    check("报告 added 正确", [c.path for c in rep.added] == [("a", "b", "c")])

    rep2 = eng.update_function_source("c", "def c():\n    pass")
    check("破环后无环", eng.cycles == [])
    check("报告 removed 正确", [c.path for c in rep2.removed] == [("a", "b", "c")])


def test_incremental_remove_function():
    print("== 增量更新：删除函数 ==")
    src = "def a():\n    b()\ndef b():\n    a()\n"
    model = parse_module(src)
    g = CallGraph.from_model(model)
    eng = IncrementalEngine(g, model)
    check("初始有相互递归环", len(eng.cycles) == 1)
    eng.remove_function("b")
    check("删除 b 后无环", eng.cycles == [])
    check("a 的出边转为外部节点", g.has_edge("a", "<external:b>"))


def test_incremental_vs_full_rebuild_random():
    print("== 增量 vs 全量重建：随机多轮一致性 ==")
    rng = random.Random(20260926)
    names = [f"f{i}" for i in range(30)]
    externals = ["<external:print>", "<external:len>"]

    def rand_calls():
        calls = []
        for _ in range(rng.randint(0, 4)):
            if rng.random() < 0.15:
                tgt = rng.choice(externals)
            else:
                tgt = rng.choice(names)
            certain = rng.random() > 0.2
            calls.append((tgt, certain, "direct" if certain else "dynamic-other"))
        return calls

    # 初始图
    g = CallGraph()
    for n in names:
        g.replace_calls(n, rand_calls())
    eng = IncrementalEngine(g)
    check("初始环均可验证", all(verify_cycle(g, c) for c in eng.cycles))

    total_added = 0
    for step in range(60):
        f = rng.choice(names)
        rep = eng.update_calls(f, rand_calls())
        total_added += len(rep.added)
        # 全量重建
        full = find_cycles(g)
        inc_paths = paths(eng.cycles)
        full_paths = paths(full)
        if inc_paths != full_paths:
            check(f"第 {step} 轮增量==全量", False,
                  f"\n    inc={inc_paths}\n    full={full_paths}")
            return
        if not all(verify_cycle(g, c) for c in eng.cycles):
            check(f"第 {step} 轮环路径可验证", False)
            return
    check("60 轮随机编辑：增量结果与全量重建完全一致", True)
    check("过程中确实检测到过环变化", total_added > 0)


def test_incremental_deep_chain_region():
    print("== 增量更新：深链中部改动 ==")
    n = 2000
    g = CallGraph()
    for i in range(n - 1):
        g.add_call(f"f{i}", f"f{i+1}", True, "direct")
    eng = IncrementalEngine(g)
    # 让 f1000 调回 f999 -> 环 (f999, f1000)
    rep = eng.update_calls("f1000", [("f999", True, "direct")])
    check("深链中部成环", paths(eng.cycles) == [("f1000", "f999")])
    check("受影响子图覆盖反向链（f0..f1000）", rep.region_size == 1001,
          str(rep.region_size))
    full = find_cycles(g)
    check("与全量重建一致", paths(full) == paths(eng.cycles))


# ---------------------------------------------------------------- 报告
def test_report_output():
    print("== 报告输出 ==")
    src = (
        "def a():\n    b()\n"
        "def b():\n    a()\n"
        "def r():\n    r()\n"
        "def free():\n    pass\n"
    )
    g = CallGraph.from_source(src)
    text = format_report(g, find_cycles(g))
    check("报告含直接递归计数", "直接递归 1" in text)
    check("报告含相互递归计数", "相互递归 1" in text)
    check("报告含环路径", "a -> b -> a" in text and "r -> r" in text)
    check("报告含涉及函数统计", "涉及函数 3 个" in text)


def main():
    test_empty_graph()
    test_no_calls()
    test_deep_chain()
    test_self_recursion()
    test_mutual_recursion()
    test_interleaved_cycles()
    test_uncertain_edges_kept()
    test_potential_cycle_via_dynamic_dispatch()
    test_cycle_path_verification()
    test_incremental_add_and_break_cycle()
    test_incremental_remove_function()
    test_incremental_vs_full_rebuild_random()
    test_incremental_deep_chain_region()
    test_report_output()
    print()
    print(f"合计: {PASS} 通过, {FAIL} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
