"""演示：迁移计划样例、进度查询、崩溃恢复。

运行：python3 demo.py
"""
import os
import tempfile
import threading
import time

from rebalance import (Cluster, CrashInjector, Migrator, SimulatedCrash,
                       compute_plan, recover_cluster)


def show_plan(title, P, old, new):
    plan = compute_plan(P, old, new)
    print(f"\n=== {title} ===")
    print(plan.summary())
    return plan


def main():
    # 1) 迁移计划样例
    show_plan("扩容 5 -> 7 节点, 64 分区", 64,
              [f"n{i}" for i in range(5)], [f"n{i}" for i in range(7)])
    show_plan("节点全部替换 3 -> 3, 32 分区", 32,
              ["a", "b", "c"], ["x", "y", "z"])
    show_plan("节点数 > 分区数 (5 节点, 3 分区)", 3,
              ["a"], ["a", "b", "c", "d", "e"])

    # 2) 并发读写下的迁移 + 进度查询
    print("\n=== 迁移过程与进度（并发读写不中断）===")
    P, old, new = 32, ["a", "b", "c"], ["b", "c", "d", "e"]
    c = Cluster(old, P)
    for pid in range(P):
        for k in range(100):
            c.write(pid, f"key-{pid}-{k}", f"val-{k}")
    plan = compute_plan(P, old, new)
    d = tempfile.mkdtemp()
    m = Migrator.start(c, plan, os.path.join(d, "journal.json"),
                       chunk_keys=8, byte_rate=200_000)  # 限速 200KB/s 便于观察

    stop = threading.Event()
    stats = {"writes": 0}

    def writer():
        i = 0
        while not stop.is_set():
            c.increment(i % P, f"hot-{i % 10}")
            stats["writes"] += 1
            i += 1

    t = threading.Thread(target=writer)
    t.start()
    done_snapshots = set()
    while not m.progress()["complete"]:
        # 单线程迁移：在主线程逐步执行以便采样进度
        st = m.journal.state
        for move in st["moves"]:
            if move["state"] != "done":
                m._migrate(move)
                break
        p = m.progress()
        snap = (p["done"], p["total"])
        if snap not in done_snapshots:
            done_snapshots.add(snap)
            print(f"  progress: {p['done']}/{p['total']} "
                  f"({p['percent']}%) bytes_copied={p['bytes_copied']}")
        if all(mv["state"] == "done" for mv in st["moves"]):
            st["complete"] = True
            m.journal.save()
    stop.set()
    t.join()
    print(f"  迁移完成，期间并发写入 {stats['writes']} 次，全部成功")
    assert c.table == plan.new_assignment

    # 3) 崩溃恢复演示
    print("\n=== 崩溃恢复（迁移到一半强杀）===")
    P2, old2, new2 = 16, ["a", "b"], ["b", "c", "d"]
    c2 = Cluster(old2, P2)
    for pid in range(P2):
        for k in range(20):
            c2.write(pid, f"k{pid}-{k}", f"v{k}")
    plan2 = compute_plan(P2, old2, new2)
    j2 = os.path.join(d, "journal2.json")
    m2 = Migrator.start(c2, plan2, j2,
                        checkpoint_hook=CrashInjector(plan2.num_moves))
    try:
        m2.run()
    except SimulatedCrash as e:
        print(f"  模拟强杀: {e}")
    p = m2.progress()
    print(f"  崩溃时进度: done={p['done']} copying={p['copying']} "
          f"pending={p['pending']}")
    c3, m3 = recover_cluster(j2, c2.nodes)
    m3.run()
    p = m3.progress()
    print(f"  恢复后完成: {p['done']}/{p['total']} complete={p['complete']}")
    ok = all(c3.read(pid, f"k{pid}-{k}") == f"v{k}"
             for pid in range(P2) for k in range(20))
    print(f"  数据校验: {'通过' if ok else '失败'}; "
          f"最终归属与计划一致: {c3.table == plan2.new_assignment}")


if __name__ == "__main__":
    main()
