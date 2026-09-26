"""注入的测试集：稳定通过、稳定失败、以及已知失败率的不稳定测试。

不稳定测试的随机性由 FLAKY_SEED 环境变量驱动（框架每轮写入），
因此对同一个 seed 完全可复现，便于审计与调试。
"""
import hashlib
import os
import random


def _seeded_rng(salt: str) -> random.Random:
    seed = int(os.environ.get("FLAKY_SEED", "0"))
    const = int.from_bytes(hashlib.md5(salt.encode()).digest()[:4], "little")
    return random.Random(seed ^ const)


# ---- 20 个稳定通过的测试（用于误判率统计）----
def _make_stable(i):
    def test():
        assert 1 + i == i + 1
    return test


for _i in range(20):
    globals()[f"test_stable_{_i:02d}"] = _make_stable(_i)


# ---- 1 个稳定失败的测试 ----
def test_stable_fail():
    assert False, "这个测试永远失败（模拟真实 bug）"


# ---- 已知失败率的不稳定测试 ----
def _make_flaky(p):
    def test():
        rng = _seeded_rng(test.__name__)
        assert rng.random() >= p, f"注入的随机失败 (p={p})"
    return test


test_flaky_p010 = _make_flaky(0.10)
test_flaky_p030 = _make_flaky(0.30)
test_flaky_p050 = _make_flaky(0.50)
test_flaky_p080 = _make_flaky(0.80)
