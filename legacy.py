"""原始实现（重构前）：物流履约方式与运费判定。

输入：
    level   客户等级: "vip" | "member" | "guest"
    amount  订单金额（元）
    weight  重量（kg）
    region  区域: "local" | "remote" | "overseas"
    fragile 是否易碎品

输出：(履约方式, 运费)
    履约方式 ∈ {"drone", "express", "standard", "freight", "reject"}
"""


def decide(level, amount, weight, region, fragile):
    # 海外订单单独处理
    if region == "overseas":
        if level == "vip":
            if weight <= 20:
                return ("express", 0.0)
            else:
                return ("freight", 200.0)
        else:
            if weight > 30:
                return ("reject", 0.0)
            else:
                if amount >= 1000:
                    return ("express", 50.0)
                else:
                    return ("standard", 120.0)
    else:
        # 易碎品优先于其它一切非海外规则
        if fragile:
            if weight > 10:
                return ("freight", 80.0 + 2.0 * weight)
            else:
                return ("express", 30.0)
        else:
            if region == "remote":
                if level == "vip":
                    if amount >= 500:
                        return ("express", 0.0)
                    else:
                        if weight > 15:
                            return ("freight", 100.0)
                        else:
                            return ("standard", 40.0)
                else:
                    if weight > 15:
                        return ("freight", 100.0)
                    else:
                        return ("standard", 40.0)
            else:
                # 本地订单
                if level == "vip":
                    if amount >= 300:
                        return ("drone", 0.0)
                    else:
                        return ("express", 0.0)
                else:
                    if level == "member":
                        if amount >= 200:
                            return ("express", 0.0)
                        else:
                            return ("standard", 10.0)
                    else:
                        # guest
                        if amount >= 500:
                            return ("express", 20.0)
                        else:
                            if weight > 5:
                                return ("standard", 15.0)
                            else:
                                return ("standard", 8.0)
