"""自测与实验入口。

运行: python3 selftest.py

包含:
  1. 边界测试: 空存储 / 单条数据 / 容量小于单条数据 / 全部数据为冷 / 覆盖写与删除
  2. 对拍测试: 随机操作序列下与 dict 参照实现逐 key 比对
  3. 策略对比实验: lru / lfu / hybrid / +prefetch / +eager_demote 的
     命中率、平均读延迟、磁盘读写量
  4. 热点注入实验: 突发切换热点集合, 统计各策略的收敛时间
"""

from __future__ import annotations

import random
import shutil
import tempfile
import os

from tiered_store import TieredStore

BASE_DIR = tempfile.mkdtemp(prefix="tiered_store_test_")


def fresh_dir(name: str) -> str:
    path = os.path.join(BASE_DIR, name)
    os.makedirs(path, exist_ok=True)
    return path


# ---------------------------------------------------------------- 边界测试

def test_empty_store():
    s = TieredStore(1024, fresh_dir("empty"))
    assert len(s) == 0
    assert "k" not in s
    try:
        s.get("missing")
        raise AssertionError("expected KeyError")
    except KeyError:
        pass
    try:
        s.delete("missing")
        raise AssertionError("expected KeyError")
    except KeyError:
        pass
    print("  ok: 空存储 (get/delete 缺失 key 抛 KeyError, len==0)")


def test_single_item():
    s = TieredStore(1024, fresh_dir("single"))
    s.put("only", "hello")
    assert len(s) == 1
    assert "only" in s
    assert s.get("only") == b"hello"
    s.put("only", "world")  # 覆盖写
    assert len(s) == 1
    assert s.get("only") == b"world"
    s.delete("only")
    assert len(s) == 0
    print("  ok: 单条数据 (put/get/覆盖写/delete)")


def test_tiny_capacity():
    # 热层容量 (10B) 小于单条数据 (>=64B)
    s = TieredStore(10, fresh_dir("tiny"))
    s.put("big", "x" * 64)
    assert s.get("big") == b"x" * 64
    assert "big" in s.cold_keys, "放不下的数据应直接落盘"
    s.put("big2", "y" * 128)
    assert s.get("big2") == b"y" * 128
    assert s.get("big") == b"x" * 64  # 反复读仍正确
    print("  ok: 容量小于单条数据 (直接落盘, 读取仍正确)")


def test_all_cold():
    # 容量为 0: 所有数据都在冷层
    s = TieredStore(0, fresh_dir("allcold"))
    for i in range(50):
        s.put(f"k{i}", f"v{i}")
    assert len(s.hot_keys) == 0
    for i in range(50):
        assert s.get(f"k{i}") == f"v{i}".encode()
    assert len(s.hot_keys) == 0, "容量为 0 时热层必须保持为空"
    m = s.metrics.snapshot()
    assert m["disk_read_ops"] == 50 and m["hot_hits"] == 0
    print("  ok: 全部数据为冷 (capacity=0, 50 条全部走磁盘且读正确)")


def test_overwrite_cold_and_delete():
    s = TieredStore(128, fresh_dir("overwrite"))
    for i in range(20):
        s.put(f"k{i}", f"value-{i}-" + "a" * 20)
    # 此时大部分 key 已下沉; 覆盖写冷数据
    for i in range(20):
        s.put(f"k{i}", f"new-{i}")
    for i in range(20):
        assert s.get(f"k{i}") == f"new-{i}".encode()
    for i in range(0, 20, 2):
        s.delete(f"k{i}")
    for i in range(20):
        if i % 2 == 0:
            assert f"k{i}" not in s
        else:
            assert s.get(f"k{i}") == f"new-{i}".encode()
    print("  ok: 冷数据覆盖写与删除 (磁盘文件同步清理)")


# ---------------------------------------------------------------- 对拍测试

def differential_test(seed: int, ops: int, capacity: int, nkeys: int, **kw):
    rng = random.Random(seed)
    store = TieredStore(capacity, fresh_dir(f"diff_{seed}"), **kw)
    oracle: dict[str, bytes] = {}
    for _ in range(ops):
        op = rng.random()
        key = f"k{rng.randrange(nkeys)}"
        if op < 0.45:
            value = ("%x" % rng.getrandbits(64)) * rng.randrange(1, 8)
            store.put(key, value)
            oracle[key] = value.encode()
        elif op < 0.9:
            try:
                got = store.get(key)
            except KeyError:
                got = None
            assert got == oracle.get(key), f"对拍失败 key={key}: {got!r} != {oracle.get(key)!r}"
        else:
            try:
                store.delete(key)
            except KeyError:
                pass
            oracle.pop(key, None)
    # 全量终态比对
    assert len(store) == len(oracle)
    for key, want in oracle.items():
        assert store.get(key) == want, f"终态对拍失败 key={key}"
    return store.metrics.snapshot()


def run_differential():
    configs = [
        dict(seed=1, ops=20000, capacity=4096, nkeys=300, policy="lru"),
        dict(seed=2, ops=20000, capacity=4096, nkeys=300, policy="lfu"),
        dict(seed=3, ops=20000, capacity=4096, nkeys=300, policy="hybrid"),
        dict(seed=4, ops=20000, capacity=2048, nkeys=300, policy="hybrid",
             prefetch=True, prefetch_count=8),
        dict(seed=5, ops=20000, capacity=2048, nkeys=300, policy="hybrid",
             eager_demote=True, demote_threshold=0.7),
        dict(seed=6, ops=20000, capacity=64, nkeys=100, policy="hybrid",
             prefetch=True, eager_demote=True),  # 容量接近单条数据大小
        dict(seed=7, ops=10000, capacity=0, nkeys=100, policy="hybrid"),  # 全冷
    ]
    for cfg in configs:
        m = differential_test(**cfg)
        print(f"  ok: 对拍通过 seed={cfg['seed']} policy={cfg['policy']} "
              f"cap={cfg['capacity']}B ops={cfg['ops']} "
              f"(hit_rate={m['hit_rate']}, disk_r={m['disk_read_ops']}, "
              f"disk_w={m['disk_write_ops']})")


# ---------------------------------------------------------------- 策略对比实验

VALUE_SIZE = 200
N_KEYS = 2000
HOT_SET = 200          # zipf 头部约 200 个 key 承担大部分访问
CAPACITY = VALUE_SIZE * 300   # 热层约容纳 300 条 = 总数据的 15%


def make_value(i: int) -> bytes:
    return f"v{i}".encode().ljust(VALUE_SIZE, b".")


def zipf_workload(rng: random.Random, n: int, hot_offset: int = 0,
                  hot_share: float = 0.9):
    """生成访问序列: hot_share 概率落在 200 个热点 key 上。"""
    keys = []
    for _ in range(n):
        if rng.random() < hot_share:
            keys.append(f"k{hot_offset + rng.randrange(HOT_SET)}")
        else:
            keys.append(f"k{rng.randrange(N_KEYS)}")
    return keys


def run_strategy_experiment():
    print("\n[实验 1] 策略对比: zipf 负载, 2000 keys x 200B, 热层容量 15%, 30000 次读")
    rng = random.Random(42)
    workload = zipf_workload(rng, 30000)
    configs = [
        ("lru", dict(policy="lru")),
        ("lfu", dict(policy="lfu")),
        ("hybrid", dict(policy="hybrid")),
        ("hybrid+prefetch", dict(policy="hybrid", prefetch=True, prefetch_count=8)),
        ("hybrid+eager", dict(policy="hybrid", eager_demote=True, demote_threshold=0.8)),
    ]
    header = f"  {'strategy':<18} {'hit_rate':>9} {'avg_lat(us)':>12} {'disk_r_ops':>11} {'disk_w_ops':>11} {'disk_r_KB':>10} {'disk_w_KB':>10}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for name, kw in configs:
        s = TieredStore(CAPACITY, fresh_dir(f"exp1_{name}"), **kw)
        for i in range(N_KEYS):
            s.put(f"k{i}", make_value(i))
        s.metrics = type(s.metrics)()  # 只统计读阶段
        for key in workload:
            s.get(key)
        m = s.metrics.snapshot()
        print(f"  {name:<18} {m['hit_rate']:>9.4f} {m['avg_read_latency_us']:>12.2f} "
              f"{m['disk_read_ops']:>11} {m['disk_write_ops']:>11} "
              f"{m['disk_read_bytes']/1024:>10.0f} {m['disk_write_bytes']/1024:>10.0f}")


# ---------------------------------------------------------------- 热点注入实验

def run_hotspot_experiment():
    print("\n[实验 2] 突发热点注入: 阶段1热点 k0..k199, 阶段2突切到 k1000..k1199")
    print("  收敛标准: 新热点集合 >=90% 驻留热层; 同时报告切换后窗口命中率")
    configs = [
        ("lru", dict(policy="lru")),
        ("lfu", dict(policy="lfu")),
        ("hybrid", dict(policy="hybrid")),
        ("hybrid+prefetch", dict(policy="hybrid", prefetch=True, prefetch_count=8)),
    ]
    header = f"  {'strategy':<18} {'收敛op数':>10} {'切换后命中率':>14} {'阶段2磁盘读':>12}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    new_hot = {f"k{1000 + i}" for i in range(HOT_SET)}
    for name, kw in configs:
        rng = random.Random(7)
        s = TieredStore(CAPACITY, fresh_dir(f"exp2_{name}"), **kw)
        for i in range(N_KEYS):
            s.put(f"k{i}", make_value(i))
        # 阶段 1: 旧热点充分预热
        for key in zipf_workload(rng, 20000, hot_offset=0):
            s.get(key)
        initial_resident = len(new_hot & s.hot_keys) / len(new_hot)
        assert initial_resident < 0.5, f"前置条件: 新热点尚未大量进入热层 ({initial_resident:.2f})"
        # 阶段 2: 突发切换到新热点
        s.metrics = type(s.metrics)()
        converge_at = None
        ops = 0
        hits_at_converge = None
        for key in zipf_workload(rng, 20000, hot_offset=1000):
            s.get(key)
            ops += 1
            if converge_at is None:
                resident = len(new_hot & s.hot_keys) / len(new_hot)
                if resident >= 0.9:
                    converge_at = ops
                    hits_at_converge = s.metrics.hot_hits
        m = s.metrics.snapshot()
        post_hits = m["hot_hits"] - (hits_at_converge or 0)
        post_reads = m["reads"] - (converge_at or 0)
        post_rate = post_hits / post_reads if post_reads else 0.0
        print(f"  {name:<18} {str(converge_at):>10} {post_rate:>14.4f} "
              f"{m['disk_read_ops']:>12}")


# ---------------------------------------------------------------- main

def main():
    print("[边界测试]")
    test_empty_store()
    test_single_item()
    test_tiny_capacity()
    test_all_cold()
    test_overwrite_cold_and_delete()

    print("\n[对拍测试] 随机 put/get/delete vs dict 参照实现")
    run_differential()

    run_strategy_experiment()
    run_hotspot_experiment()

    shutil.rmtree(BASE_DIR, ignore_errors=True)
    print("\n全部测试与实验完成。")


if __name__ == "__main__":
    main()
