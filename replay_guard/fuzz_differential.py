"""对拍脚本：滑动窗口实现 vs 朴素全量记录实现。

随机生成包含乱序、倒退、跳跃、重复 id 的请求序列，逐条比对两者判定，
任何一步不一致即打印反例并以非零码退出。

用法：python3 fuzz_differential.py [轮数] [每轮步数] [种子]
"""

import random
import sys

from naive_protector import NaiveProtector
from replay_protector import ReplayProtector


def gen_sequence(rng, steps, window, skew):
    """生成 (request_id, timestamp, now) 序列，覆盖各类边界情形。"""
    seq = []
    now = 10_000.0
    max_ts = now
    id_pool = [f"id-{i}" for i in range(50)]
    sent = []  # (id, ts) 已发送，用于构造真实重放
    for _ in range(steps):
        kind = rng.random()
        if kind < 0.30 and sent:
            # 真实重放：原样重发（可能已出窗）
            rid, ts = sent[rng.randrange(len(sent))]
        elif kind < 0.45 and sent:
            # 同 id 不同内容/时间戳
            rid = sent[rng.randrange(len(sent))][0]
            ts = max_ts + rng.uniform(-window * 1.5, window * 0.5)
        elif kind < 0.60:
            # 窗口内乱序/倒退
            rid = rng.choice(id_pool) + f"-{rng.randrange(10**6)}"
            ts = max_ts - rng.uniform(0, window)
        elif kind < 0.70:
            # 边界：恰好压在窗口下界 / 恰好压在漂移上界
            rid = f"edge-{rng.randrange(10**6)}"
            ts = rng.choice([max_ts - window, now + skew])
        elif kind < 0.80:
            # 大跳跃（时钟同步前进）
            jump = rng.uniform(window, 20 * window)
            now += jump
            max_ts = now + rng.uniform(-skew, skew)
            rid = f"jump-{rng.randrange(10**6)}"
            ts = max_ts
        elif kind < 0.90:
            # 超出漂移的未来时间戳
            rid = f"future-{rng.randrange(10**6)}"
            ts = now + skew + rng.uniform(1e-9, window)
        else:
            # 正常前进
            step = rng.uniform(0, window / 10)
            now += step
            max_ts = max(max_ts, now + rng.uniform(-skew, skew))
            rid = f"norm-{rng.randrange(10**6)}"
            ts = max_ts
        seq.append((rid, ts, now))
        sent.append((rid, ts))
    return seq


def run_round(seed, steps):
    rng = random.Random(seed)
    window = rng.choice([0.0, 1.0, 60.0, 300.0])
    skew = rng.choice([0.0, 5.0, 30.0])
    seq = gen_sequence(rng, steps, window, skew)
    sliding = ReplayProtector(window, skew)
    naive = NaiveProtector(window, skew)
    for i, (rid, ts, now) in enumerate(seq):
        d1 = sliding.check(rid, ts, now=now)
        d2 = naive.check(rid, ts, now=now)
        if (d1.accepted, d1.reason) != (d2.accepted, d2.reason):
            print(f"DIVERGENCE seed={seed} step={i} window={window} skew={skew}")
            print(f"  request: id={rid!r} ts={ts} now={now}")
            print(f"  sliding: accepted={d1.accepted} reason={d1.reason}")
            print(f"  naive:   accepted={d2.accepted} reason={d2.reason}")
            return False
    return True


def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    steps = int(sys.argv[2]) if len(sys.argv) > 2 else 2000
    base_seed = int(sys.argv[3]) if len(sys.argv) > 3 else 42
    for r in range(rounds):
        if not run_round(base_seed + r, steps):
            sys.exit(1)
    print(f"OK: {rounds} 轮 x {steps} 步，两边拒绝集合完全一致")


if __name__ == "__main__":
    main()
