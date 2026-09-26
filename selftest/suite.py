"""自测用测试集：人为注入各类已知行为，用来验证框架判定是否正确。

包含四类被测对象：
  1. 稳定通过（5 个）—— 框架绝不能把它们误判为不稳定；
  2. 稳定失败（1 个）—— 每次都失败，验证「稳定失败」判定；
  3. 随机不稳定（1 个）—— 约 30% 概率失败，验证「不稳定」判定；
  4. 顺序相关不稳定（1 对）—— 仅在 test_order_source 先执行时失败，
     验证固定顺序 vs 打乱顺序的对比分析。
"""
import random

# 模块级状态：框架每轮在独立子进程中执行，因此每轮状态都是干净的。
_state = {}


# ---- 1. 稳定通过 ----
def test_stable_addition():
    assert 1 + 1 == 2


def test_stable_string():
    assert "flaky".upper() == "FLAKY"


def test_stable_list():
    assert sorted([3, 1, 2]) == [1, 2, 3]


def test_stable_dict():
    d = {"a": 1}
    assert d.get("a") == 1 and d.get("b") is None


def test_stable_tuple():
    assert (1, 2) + (3,) == (1, 2, 3)


# ---- 2. 稳定失败 ----
def test_stable_failure():
    assert False, "故意注入的稳定失败：每次必失败"


# ---- 3. 随机不稳定（约 30% 失败率）----
def test_flaky_random():
    if random.random() < 0.3:
        raise AssertionError("注入的随机不稳定失败")


# ---- 4. 顺序相关不稳定 ----
# 定义顺序保证：固定顺序下 victim 先于 source 执行 -> 固定模式永远通过；
# 打乱顺序下约一半轮次 source 先执行 -> victim 失败。
def test_order_victim():
    if _state.get("source_ran"):
        raise AssertionError("顺序相关失败：test_order_source 已先执行")


def test_order_source():
    _state["source_ran"] = True
