"""示例查询集：同一套 Plan 同时用于串行基线与并行执行（对拍基础）。"""

from .plan import (OrderKey, Plan, avg, collect_distinct, count_distinct,
                   count_star, max_, min_, sum_)
from . import expr as E

# ---- golden 数据集：events(user_id:int, region:str, amount:int, score:float, status:str, ts:int) ----

EVENTS_COLS = [
    ("user_id", "int"), ("region", "str"), ("amount", "int"),
    ("score", "float"), ("status", "str"), ("ts", "int"),
]

Q_SELECT = Plan(
    # 明细：过滤 + 投影，按全局行号保序（无 order_by 时即扫描序）
    filter=E.gt(E.col("amount"), E.lit(10)),
    select=(
        ("user_id", E.col("user_id")),
        ("region", E.col("region")),
        ("amount", E.col("amount")),
        ("status", E.col("status")),
    ),
)

Q_ORDER = Plan(
    # 跨分片排序：score 降序，同分时按 ts 升序，再同名次由行号兜底
    filter=E.notnull(E.col("score")),
    select=(
        ("user_id", E.col("user_id")),
        ("region", E.col("region")),
        ("score", E.col("score")),
        ("ts", E.col("ts")),
    ),
    order_by=(
        OrderKey(E.col("score"), desc=True),
        OrderKey(E.col("ts"), desc=False),
    ),
    limit=20,
)

Q_AGG_REGION = Plan(
    # 跨分片聚合：按 region 分组，五种口径一次覆盖
    group_by=(E.col("region"),),
    aggs=(
        count_star("cnt"),
        sum_(E.col("amount"), "amount_sum"),
        min_(E.col("amount"), "amount_min"),
        max_(E.col("amount"), "amount_max"),
        avg(E.col("amount"), "amount_avg"),
        count_distinct(E.col("user_id"), "users_distinct"),
        collect_distinct(E.col("status"), "statuses"),
    ),
)

Q_AGG_USER = Plan(
    # 倾斜场景：按 user_id 分组（少数用户占绝大多数行）
    filter=E.eq(E.col("status"), E.lit("ok")),
    group_by=(E.col("user_id"),),
    aggs=(
        count_star("ok_cnt"),
        sum_(E.col("amount"), "ok_amount_sum"),
        max_(E.col("score"), "score_max"),
        count_distinct(E.col("region"), "regions_distinct"),
    ),
    order_by=(OrderKey(E.col("ok_cnt"), desc=True),
              OrderKey(E.col("key_0"), desc=False)),
)

Q_AGG_GLOBAL = Plan(
    # 无 group by：全局标量聚合（空结果时仍应输出一行，avg 为 None）
    filter=E.eq(E.col("status"), E.lit("nonexistent_status")),
    aggs=(
        count_star("cnt"),
        sum_(E.col("amount"), "amount_sum"),
        avg(E.col("amount"), "amount_avg"),
        min_(E.col("amount"), "amount_min"),
        max_(E.col("amount"), "amount_max"),
    ),
)

Q_AGG_REGION_NULL = Plan(
    # 分组键含 NULL：NULL 应自成一组
    group_by=(E.col("status"),),
    aggs=(count_star("cnt"), sum_(E.col("amount"), "amount_sum")),
)

ALL_QUERIES = {
    "select_filter": Q_SELECT,
    "order_cross_shard": Q_ORDER,
    "agg_region": Q_AGG_REGION,
    "agg_user_skew": Q_AGG_USER,
    "agg_global_empty": Q_AGG_GLOBAL,
    "agg_null_group": Q_AGG_REGION_NULL,
}
