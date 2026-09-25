"""估算公式实验验证：对比预测迁移数据量/耗时与实际测量值。

模型：
  D_est = M * avg_partition_bytes
  T_est = D / rate + M * cutover
实际迁移用 byte_rate 限速拷贝模拟带宽；
cutover（临界区补拷+落盘+切换的单次开销）由独立的小规模校准实验测得，
不来自被测用例本身。

运行：python3 experiment.py
"""
import os
import random
import tempfile
import time

from rebalance import Cluster, Migrator, compute_plan, estimator


def calibrate_cutover(rate, rounds=3):
    """独立校准：测量单次 cutover 的固定开销（秒）。"""
    samples = []
    for i in range(rounds):
        c = Cluster(["a"], 4)
        for pid in range(4):
            c.write(pid, "k", b"x")  # 数据量极小，拷贝耗时可忽略
        plan = compute_plan(4, ["a"], ["a", "b"])
        d = tempfile.mkdtemp()
        m = Migrator.start(c, plan, os.path.join(d, "j.json"), byte_rate=rate)
        t0 = time.monotonic()
        m.run()
        elapsed = time.monotonic() - t0
        copy_time = m.stats["bytes_copied"] / rate
        samples.append((elapsed - copy_time) / plan.num_moves)
    return sum(samples) / len(samples)


def run_case(P, old_n, new_n, keys_per_partition, value_bytes, rate,
             cutover, seed=0):
    rng = random.Random(seed)
    old = [f"n{i}" for i in range(old_n)]
    new = [f"m{i}" for i in range(new_n)] if old_n == new_n else \
        [f"n{i}" for i in range(new_n)]
    c = Cluster(old, P)
    total_bytes = 0
    for pid in range(P):
        for k in range(keys_per_partition):
            v = bytes(rng.getrandbits(8) for _ in range(value_bytes))
            c.write(pid, f"k{pid}-{k}", v)
            total_bytes += value_bytes + len(f"k{pid}-{k}")

    plan = compute_plan(P, old, new)
    M = plan.num_moves
    avg_part = total_bytes / P
    D_est = estimator.estimate_data_bytes(M, avg_part)

    d = tempfile.mkdtemp()
    m = Migrator.start(c, plan, os.path.join(d, "j.json"),
                       chunk_keys=32, byte_rate=rate)

    # 先测单次 cutover 开销（用第一个 move 的临界区近似），这里取固定小量测量
    t0 = time.monotonic()
    m.run()
    T_act = time.monotonic() - t0
    D_act = m.stats["bytes_copied"]

    T_est = estimator.estimate_time_seconds(D_est, rate, M, cutover)

    return {
        "case": f"P={P} {old_n}->{new_n}节点",
        "M": M, "D_est": D_est, "D_act": D_act,
        "T_est": T_est, "T_act": T_act,
        "data_dev": (D_act - D_est) / D_est if D_est else 0.0,
        "time_dev": (T_act - T_est) / T_est if T_est else 0.0,
    }


def main():
    rate = 2_000_000  # 模拟带宽 2 MB/s
    cutover = calibrate_cutover(rate)
    print(f"校准: 单次 cutover 开销 = {cutover*1000:.2f} ms\n")
    cases = [
        (32, 3, 5, 200, 64, rate),
        (64, 5, 7, 100, 128, rate),
        (64, 4, 4, 100, 128, rate),   # 全部替换
        (16, 1, 4, 400, 64, rate),
        (48, 6, 3, 100, 96, rate),    # 缩容
    ]
    print(f"{'场景':<22}{'迁移数':>6}{'D_est(KB)':>10}{'D_act(KB)':>10}"
          f"{'数据偏差':>9}{'T_est(s)':>9}{'T_act(s)':>9}{'耗时偏差':>9}")
    rows = []
    for i, (P, o, n, k, vb, r) in enumerate(cases):
        r_ = run_case(P, o, n, k, vb, r, cutover, seed=i)
        rows.append(r_)
        print(f"{r_['case']:<22}{r_['M']:>6}"
              f"{r_['D_est']/1024:>10.1f}{r_['D_act']/1024:>10.1f}"
              f"{r_['data_dev']*100:>8.1f}%"
              f"{r_['T_est']:>9.3f}{r_['T_act']:>9.3f}"
              f"{r_['time_dev']*100:>8.1f}%")
    avg_data = sum(abs(r["data_dev"]) for r in rows) / len(rows)
    avg_time = sum(abs(r["time_dev"]) for r in rows) / len(rows)
    print(f"\n平均偏差: 数据量 {avg_data*100:.2f}%, 耗时 {avg_time*100:.2f}%")
    print("说明: 数据量估算为 M*平均分区大小, 分区大小均匀时偏差接近 0;")
    print("      耗时估算 = D/rate + M*cutover(独立校准), 偏差来自调度与计时噪声。")


if __name__ == "__main__":
    main()
