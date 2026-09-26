"""重构前的原始实现：订单处理方式的业务判定（嵌套条件版）。

保留本文件作为差分测试的行为基准（golden reference），重构后不得修改。

输入 order 字段：
    tier         会员等级: "normal" | "silver" | "gold" | "platinum"
    amount       订单金额: 数值, >= 0
    region       区域:     "domestic" | "remote" | "overseas"
    account_days 账户天数: 整数, >= 0
    coupon       是否用券: bool
    flagged      是否命中风控: bool

输出：判定结果字符串，共 6 种：
    "reject" / "manual_review" / "vip_fast" / "discount_20" / "discount_10" / "standard"
"""


def decide(order):
    tier = order["tier"]
    amount = order["amount"]
    region = order["region"]
    account_days = order["account_days"]
    coupon = order["coupon"]
    flagged = order["flagged"]

    # 风控优先：命中风控的老高价值用户转人工，其余直接拒绝
    if flagged:
        if tier in ("gold", "platinum"):
            if account_days >= 365:
                return "manual_review"
            else:
                return "reject"
        else:
            return "reject"
    else:
        # 海外订单
        if region == "overseas":
            if amount >= 10000:
                return "manual_review"
            else:
                return "standard"
        else:
            # 偏远地区订单
            if region == "remote":
                if amount >= 5000:
                    return "manual_review"
                else:
                    return "standard"
            else:
                # 国内订单按会员等级分档
                if tier == "platinum":
                    if amount >= 2000:
                        return "vip_fast"
                    else:
                        return "discount_20"
                else:
                    if tier == "gold":
                        if coupon:
                            if amount >= 1000:
                                return "discount_20"
                            else:
                                if amount >= 3000:
                                    return "vip_fast"
                                else:
                                    return "discount_10"
                        else:
                            if amount >= 3000:
                                return "vip_fast"
                            else:
                                return "discount_10"
                    else:
                        if tier == "silver":
                            if coupon:
                                if amount >= 500:
                                    return "discount_10"
                                else:
                                    return "standard"
                            else:
                                return "standard"
                        else:
                            # 普通用户
                            if coupon:
                                if amount >= 200:
                                    return "discount_10"
                                else:
                                    return "standard"
                            else:
                                return "standard"
