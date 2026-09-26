"""重构后的业务判定：规则表（数据）+ 通用引擎（rule_engine）。

新增/调整一种判定只需改动本文件的 RULES：声明条件、结果、优先级。
优先级显式编码了原嵌套 if/else 的隐式先后顺序，与声明顺序无关。
"""

from rule_engine import Engine, Rule

RULES = [
    # ---- 风控（最高优先级，对应原实现最外层的 flagged 分支）----
    Rule({"flagged": {"eq": True},
          "tier": {"in": ["gold", "platinum"]},
          "account_days": {"gte": 365}},
         "manual_review", priority=100, name="风控-老高价值人工复核"),
    Rule({"flagged": {"eq": True}},
         "reject", priority=90, name="风控-拒绝"),

    # ---- 区域（对应原实现第二层 region 分支）----
    Rule({"region": {"eq": "overseas"}, "amount": {"gte": 10000}},
         "manual_review", priority=80, name="海外大额人工复核"),
    Rule({"region": {"eq": "overseas"}},
         "standard", priority=70, name="海外标准"),
    Rule({"region": {"eq": "remote"}, "amount": {"gte": 5000}},
         "manual_review", priority=60, name="偏远大额人工复核"),
    Rule({"region": {"eq": "remote"}},
         "standard", priority=50, name="偏远标准"),

    # ---- 国内会员分档（对应原实现最内层 tier 分支）----
    Rule({"tier": {"eq": "platinum"}, "amount": {"gte": 2000}},
         "vip_fast", priority=45, name="白金大额快速通道"),
    Rule({"tier": {"eq": "platinum"}},
         "discount_20", priority=44, name="白金八折"),
    Rule({"tier": {"eq": "gold"}, "coupon": {"eq": True},
          "amount": {"gte": 1000}},
         "discount_20", priority=43, name="黄金用券八折"),
    Rule({"tier": {"eq": "gold"}, "amount": {"gte": 3000}},
         "vip_fast", priority=42, name="黄金大额快速通道"),
    Rule({"tier": {"eq": "gold"}},
         "discount_10", priority=41, name="黄金九折"),
    Rule({"tier": {"eq": "silver"}, "coupon": {"eq": True},
          "amount": {"gte": 500}},
         "discount_10", priority=40, name="白银用券九折"),
    Rule({"tier": {"eq": "silver"}},
         "standard", priority=39, name="白银标准"),
    Rule({"tier": {"eq": "normal"}, "coupon": {"eq": True},
          "amount": {"gte": 200}},
         "discount_10", priority=38, name="普通用券九折"),

    # ---- 默认分支（显式声明，对应原实现最后的 return "standard"）----
    Rule(None, "standard", priority=0, name="默认"),
]

_engine = Engine(RULES)


def decide(order):
    return _engine.decide(order)
