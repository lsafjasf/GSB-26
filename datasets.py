"""差分对拍与覆盖度分析共用的数据集定义。

独立成模块，供回归测试（test_decision.py）、覆盖度分析
（analyze_coverage.py / rule_coverage.py）复用同一份数据，
保证分析结论可复算：同样的代码版本 + 同样的种子 => 同样的数据集。
"""

import itertools
import random

# 边界值：覆盖全部比较阈值 200/500/1000/2000/3000/5000/10000 与 365 的
# 左邻、等于、右邻，以及 0 和大值。
AMOUNTS = [0, 1, 199, 200, 201, 499, 500, 501, 999, 1000, 1001,
           1999, 2000, 2001, 2999, 3000, 3001, 4999, 5000, 5001,
           9999, 10000, 10001, 10**9]
DAYS = [0, 1, 364, 365, 366, 10**6]
TIERS = ["normal", "silver", "gold", "platinum"]
REGIONS = ["domestic", "remote", "overseas"]

GRID_SIZE = 4 * 3 * len(AMOUNTS) * len(DAYS) * 2 * 2  # 6912
FUZZ_COUNT = 20000
FUZZ_SEED = 20260927


def grid_cases():
    """全组合网格：4*3*24*6*2*2 = 6912 例，覆盖所有分支与边界。"""
    for tier, region, amount, days, coupon, flagged in itertools.product(
            TIERS, REGIONS, AMOUNTS, DAYS, [False, True], [False, True]):
        yield {"tier": tier, "region": region, "amount": amount,
               "account_days": days, "coupon": coupon, "flagged": flagged}


def fuzz_cases(count=FUZZ_COUNT, seed=FUZZ_SEED):
    """确定性随机模糊：连续/整数金额混合，同一种子生成同一批用例。"""
    rng = random.Random(seed)
    for _ in range(count):
        yield {"tier": rng.choice(TIERS),
               "region": rng.choice(REGIONS),
               "amount": rng.choice([rng.uniform(0, 20000),
                                     rng.randint(0, 20000)]),
               "account_days": rng.randint(0, 2000),
               "coupon": rng.random() < 0.5,
               "flagged": rng.random() < 0.5}
