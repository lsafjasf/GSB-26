"""顺序相关注入测试：polluter 污染模块级状态，victim 受污染即失败。

固定顺序（按名字排序）下 polluter 总在 victim 之前执行 -> victim 每轮必败；
打乱顺序下 victim 只有约一半轮次排在 polluter 之后 -> 失败率约 50%。
victim 检查后清除污染，避免跨轮泄漏干扰实验。
"""

_state = {"polluted": False}


def test_order_a_polluter():
    _state["polluted"] = True


def test_order_b_victim():
    polluted = _state["polluted"]
    _state["polluted"] = False
    assert not polluted, "被 test_order_a_polluter 污染的共享状态导致失败"


def test_order_c_independent():
    assert True
