"""重构后实现：业务判定 = 规则表（rules.py）+ 引擎（rule_engine.py）。

本文件只负责把入参组装成上下文、调用引擎、解析结果，
不包含任何业务分支逻辑。
"""

from rule_engine import Engine
from rules import RULES

_engine = Engine(RULES)  # 加载期即完成冲突 / 不可达校验


def _ctx(level, amount, weight, region, fragile):
    return {
        "level": level,
        "amount": amount,
        "weight": weight,
        "region": region,
        "fragile": fragile,
    }


def _resolve(rule, ctx):
    method, fee = rule.result
    if callable(fee):
        fee = fee(ctx)
    return (method, fee)


def decide(level, amount, weight, region, fragile):
    ctx = _ctx(level, amount, weight, region, fragile)
    return _resolve(_engine.match(ctx), ctx)


def explain(level, amount, weight, region, fragile):
    """与 decide 同一份规则表：返回 (判定结果, 来源追踪 Trace)。"""
    ctx = _ctx(level, amount, weight, region, fragile)
    trace = _engine.explain(ctx)
    return _resolve(trace.rule, ctx), trace
