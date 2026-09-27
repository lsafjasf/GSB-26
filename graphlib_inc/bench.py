"""Benchmark: incremental vs full-recompute connectivity on a large graph.

Default workload: n = 1,000,000 vertices, m0 = 2,000,000 random edges,
then 10,000 edge updates (random insertions / deletions), each followed by
a reachability query.

* Incremental: DynamicConnectivity, cumulative update + query time.
* Full recompute: after each update the components are rebuilt from
  scratch with union-find over the whole current edge set.  Since that is
  far too slow to run 10,000 times, it is measured on a sample of the
  steps and extrapolated linearly (the per-step cost of a full recompute
  is essentially independent of the step).

At every sampled step the incremental and full-recompute query answers
are cross-checked for equality.

Run:  python3 -m graphlib_inc.bench [--n N] [--m M] [--updates U]
"""

import argparse
import json
import random
import time
from array import array

from graphlib_inc.dynamic_connectivity import DynamicConnectivity


def rss_kb():
    with open("/proc/self/status") as fh:
        for line in fh:
            if line.startswith("VmRSS"):
                return int(line.split()[1])
    return 0


def gen_edges(n, m, rng):
    """m distinct random undirected edges (u, v), u != v, as (u, v) pairs."""
    seen = set()
    edges = []
    while len(edges) < m:
        u = rng.randrange(n)
        v = rng.randrange(n)
        if u == v:
            continue
        if u > v:
            u, v = v, u
        key = u * n + v
        if key in seen:
            continue
        seen.add(key)
        edges.append((u, v))
    return edges


def full_recompute_query(n, edges, a, b):
    """Answer connected(a, b) by rebuilding union-find over all edges."""
    parent = array("i", range(n))
    size = array("i", [1]) * n

    def find(x):
        r = x
        while parent[r] != r:
            r = parent[r]
        while parent[x] != r:
            parent[x], x = r, parent[x]
        return r

    for u, v in edges:
        ru, rv = find(u), find(v)
        if ru != rv:
            if size[ru] < size[rv]:
                ru, rv = rv, ru
            parent[rv] = ru
            size[ru] += size[rv]
    return find(a) == find(b)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1_000_000, help="vertices")
    ap.add_argument("--m", type=int, default=2_000_000, help="initial edges")
    ap.add_argument("--updates", type=int, default=10_000, help="edge updates")
    ap.add_argument("--full-samples", type=int, default=25,
                    help="steps where a full recompute is timed")
    ap.add_argument("--seed", type=int, default=20260927)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    n, m0, U = args.n, args.m, args.updates

    print(f"[setup] generating {m0:,} distinct edges on {n:,} vertices ...")
    t0 = time.perf_counter()
    edges = gen_edges(n, m0, rng)
    print(f"[setup] done in {time.perf_counter() - t0:.1f}s")

    # ---------------- build incremental structure ---------------- #
    rss0 = rss_kb()
    t0 = time.perf_counter()
    dc = DynamicConnectivity()
    dc.add_edges(edges)
    build_t = time.perf_counter() - t0
    rss1 = rss_kb()
    print(f"[incremental] build: {build_t:.2f}s, "
          f"components={dc.num_components:,}, RSS={rss1 / 1024:.0f} MB")

    # ---------------- generate the update sequence ---------------- #
    # live: list of currently present edges; extra: candidate edges to add
    extra = gen_edges(n, U, rng)          # disjoint from initial edges
    live = list(edges)
    live_keys = {u * n + v for u, v in live}
    ops = []                               # ("del"/"add", (u, v), query)
    add_i = 0
    for _ in range(U):
        if rng.random() < 0.5 and live:
            i = rng.randrange(len(live))
            e = live[i]
            ops.append(("del", e))
            live[i] = live[-1]
            live.pop()
            live_keys.discard(e[0] * n + e[1])
        else:
            e = extra[add_i % len(extra)]
            add_i += 1
            ops.append(("add", e))
            live.append(e)
            live_keys.add(e[0] * n + e[1])
        a, b = rng.randrange(n), rng.randrange(n)
        ops[-1] = (ops[-1][0], ops[-1][1], (a, b))
    sample_steps = set(
        round(i * (U - 1) / (args.full_samples - 1)) for i in range(args.full_samples))

    rss_ops = rss_kb()

    # ---------------- incremental pass ---------------- #
    print(f"[incremental] applying {U:,} updates + queries ...")
    inc_upd_t = 0.0
    inc_qry_t = 0.0
    answers_inc = {}
    for step, (kind, e, q) in enumerate(ops):
        t0 = time.perf_counter()
        if kind == "del":
            dc.remove_edge(*e)
        else:
            dc.add_edge(*e)
        t1 = time.perf_counter()
        ans = dc.connected(*q)
        t2 = time.perf_counter()
        inc_upd_t += t1 - t0
        inc_qry_t += t2 - t1
        if step in sample_steps:
            answers_inc[step] = ans
    rss2 = rss_kb()
    inc_total = inc_upd_t + inc_qry_t
    print(f"[incremental] updates: {inc_upd_t:.3f}s, queries: {inc_qry_t:.3f}s, "
          f"total: {inc_total:.3f}s, RSS={rss2 / 1024:.0f} MB")

    # ---------------- full-recompute pass (sampled) ---------------- #
    print(f"[full] replaying sequence, timing full recompute at "
          f"{len(sample_steps)} sampled steps ...")
    cur = list(edges)
    pos = {e: i for i, e in enumerate(cur)}   # edge -> index for O(1) removal
    full_times = []
    mismatches = 0
    for step, (kind, e, q) in enumerate(ops):
        if kind == "del":
            i = pos.pop(e)
            last = cur.pop()
            if i < len(cur):
                cur[i] = last
                pos[last] = i
        else:
            pos[e] = len(cur)
            cur.append(e)
        if step in sample_steps:
            t0 = time.perf_counter()
            ans = full_recompute_query(n, cur, *q)
            dt = time.perf_counter() - t0
            full_times.append(dt)
            if ans != answers_inc[step]:
                mismatches += 1
            print(f"  step {step:>6}: full recompute {dt:.2f}s "
                  f"({len(cur):,} edges), match={ans == answers_inc[step]}")
    avg_full = sum(full_times) / len(full_times)
    full_total_est = avg_full * U
    print(f"[full] avg recompute+query: {avg_full:.3f}s -> "
          f"estimated total for {U:,} updates: {full_total_est:.0f}s")

    # ---------------- summary ---------------- #
    speedup = full_total_est / inc_total
    print()
    print("=" * 64)
    print(f"graph: n={n:,}, m0={m0:,}, updates={U:,} (seed={args.seed})")
    print(f"incremental : build {build_t:8.2f}s | updates+queries {inc_total:8.3f}s")
    print(f"full x{U:<6}: avg {avg_full:8.3f}s | estimated total   {full_total_est:8.0f}s")
    print(f"speedup     : {speedup:,.0f}x")
    print(f"correctness : {len(sample_steps) - mismatches}/{len(sample_steps)} "
          f"sampled query answers identical")
    print(f"memory RSS  : before build {rss0 / 1024:.0f} MB -> after build "
          f"{rss1 / 1024:.0f} MB -> after workload gen {rss_ops / 1024:.0f} MB "
          f"-> after {U:,} updates {rss2 / 1024:.0f} MB "
          f"(library delta during updates {((rss2 - rss_ops) / 1024):+.1f} MB)")
    print("=" * 64)
    print(json.dumps({
        "n": n, "m0": m0, "updates": U, "seed": args.seed,
        "build_s": round(build_t, 3),
        "incremental_total_s": round(inc_total, 4),
        "incremental_updates_s": round(inc_upd_t, 4),
        "incremental_queries_s": round(inc_qry_t, 4),
        "full_recompute_avg_s": round(avg_full, 4),
        "full_recompute_est_total_s": round(full_total_est, 1),
        "speedup": round(speedup, 1),
        "sampled_mismatches": mismatches,
        "rss_mb": {"before": rss0 // 1024, "after_build": rss1 // 1024,
                   "after_workload_gen": rss_ops // 1024,
                   "after_updates": rss2 // 1024},
    }))


if __name__ == "__main__":
    main()
