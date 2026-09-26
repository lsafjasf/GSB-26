"""规则表：业务判定规则的唯一声明位置。

新增 / 修改 / 删除一种判定，只需改动本文件中的 RULES 列表。
匹配顺序完全由 priority 决定（大者优先），与声明顺序无关。
"""

from rule_engine import Rule

RULES = [
    # ---- 易碎品（非海外）：优先于其它一切非海外规则 ----
    Rule("R01", 100,
         (("region", "!=", "overseas"), ("fragile", "==", True),
          ("weight", ">", 10)),
         ("freight", lambda c: 80.0 + 2.0 * c["weight"])),
    Rule("R02", 90,
         (("region", "!=", "overseas"), ("fragile", "==", True)),
         ("express", 30.0)),

    # ---- 海外 ----
    Rule("R03", 85,
         (("region", "==", "overseas"), ("level", "==", "vip"),
          ("weight", "<=", 20)),
         ("express", 0.0)),
    Rule("R04", 84,
         (("region", "==", "overseas"), ("level", "==", "vip")),
         ("freight", 200.0)),
    # R05 与 R06 条件重叠（weight>30 且 amount>=1000 时两者皆命中），
    # 原实现先判 weight>30，故 R05 优先级必须高于 R06。
    Rule("R05", 83,
         (("region", "==", "overseas"), ("level", "!=", "vip"),
          ("weight", ">", 30)),
         ("reject", 0.0)),
    Rule("R06", 82,
         (("region", "==", "overseas"), ("level", "!=", "vip"),
          ("amount", ">=", 1000)),
         ("express", 50.0)),
    Rule("R07", 81,
         (("region", "==", "overseas"), ("level", "!=", "vip")),
         ("standard", 120.0)),

    # ---- 偏远地区（非易碎）----
    # R08 与 R09 条件重叠（vip 且 amount>=500 且 weight>15），
    # 原实现先判 vip 免运费，故 R08 优先级必须高于 R09。
    Rule("R08", 70,
         (("region", "==", "remote"), ("fragile", "==", False),
          ("level", "==", "vip"), ("amount", ">=", 500)),
         ("express", 0.0)),
    Rule("R09", 69,
         (("region", "==", "remote"), ("fragile", "==", False),
          ("weight", ">", 15)),
         ("freight", 100.0)),
    Rule("R10", 68,
         (("region", "==", "remote"), ("fragile", "==", False)),
         ("standard", 40.0)),

    # ---- 本地（非易碎）----
    Rule("R11", 60,
         (("region", "==", "local"), ("fragile", "==", False),
          ("level", "==", "vip"), ("amount", ">=", 300)),
         ("drone", 0.0)),
    Rule("R12", 59,
         (("region", "==", "local"), ("fragile", "==", False),
          ("level", "==", "vip")),
         ("express", 0.0)),
    Rule("R13", 58,
         (("region", "==", "local"), ("fragile", "==", False),
          ("level", "==", "member"), ("amount", ">=", 200)),
         ("express", 0.0)),
    Rule("R14", 57,
         (("region", "==", "local"), ("fragile", "==", False),
          ("level", "==", "member")),
         ("standard", 10.0)),
    Rule("R15", 56,
         (("region", "==", "local"), ("fragile", "==", False),
          ("level", "==", "guest"), ("amount", ">=", 500)),
         ("express", 20.0)),
    Rule("R16", 55,
         (("region", "==", "local"), ("fragile", "==", False),
          ("level", "==", "guest"), ("weight", ">", 5)),
         ("standard", 15.0)),

    # ---- 默认分支（显式声明，必须恰好一条）----
    Rule("DEFAULT", 0, (), ("standard", 8.0), default=True),
]
