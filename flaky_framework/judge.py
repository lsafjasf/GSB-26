"""稳定性统计判定。

判定规则（阈值见 README「判定阈值说明」）：
  - n 次重复中失败 f 次：
    - f == 0   -> 稳定通过。置信度 = 1-(1-p0)^n：若真实失败率 >= p0，
                  连续 n 次全通过的概率只有 (1-p0)^n。
    - f == n   -> 稳定失败。置信度同上（对通过率的对称论证）。
    - 0 < f < n -> 不稳定。同一份代码出现两种结果就是不稳定的行为学证据，
                  判定本身置信度为 1；失败率点估计 f/n，附 Wilson 95% 置信区间。
  - p0（最小可检测失败率）默认 0.05，可用 min_rate 调整。
"""
from __future__ import annotations

import math

STABLE_PASS = "稳定通过"
STABLE_FAIL = "稳定失败"
FLAKY = "不稳定"
INSUFFICIENT = "样本不足"

DEFAULT_MIN_RATE = 0.05   # p0：最小可检测失败率
DEFAULT_Z = 1.96          # 95% 置信区间对应的 z 值


def wilson_interval(k, n, z=DEFAULT_Z):
    """Wilson score 置信区间，小样本下比正态近似更稳。"""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def classify(failures, n, min_rate=DEFAULT_MIN_RATE, z=DEFAULT_Z):
    """根据 n 次重复中的失败次数给出判定。返回 dict。"""
    if n <= 0:
        return {"label": INSUFFICIENT, "confidence": 0.0,
                "rate": None, "ci": None,
                "reason": "没有执行记录，无法判定。"}

    rate = failures / n
    ci = wilson_interval(failures, n, z)

    if failures == 0:
        conf = 1 - (1 - min_rate) ** n
        return {"label": STABLE_PASS, "confidence": conf, "rate": 0.0, "ci": ci,
                "reason": (f"{n} 次重复全部通过。若真实失败率≥{min_rate:.0%}，"
                           f"出现该结果的概率仅 {(1 - min_rate) ** n:.2%}，"
                           f"故以 {conf:.1%} 置信度认为失败率 < {min_rate:.0%}。")}
    if failures == n:
        conf = 1 - (1 - min_rate) ** n
        return {"label": STABLE_FAIL, "confidence": conf, "rate": 1.0, "ci": ci,
                "reason": (f"{n} 次重复全部失败。以 {conf:.1%} 置信度认为"
                           f"通过率 < {min_rate:.0%}（对称论证）。")}
    return {"label": FLAKY, "confidence": 1.0, "rate": rate, "ci": ci,
            "reason": (f"{n} 次重复中失败 {failures} 次（{rate:.1%}），"
                       f"同一代码出现通过/失败两种结果，是不稳定的直接证据。"
                       f"失败率 95% 置信区间 [{ci[0]:.1%}, {ci[1]:.1%}]。")}


def needed_repeats(min_rate=DEFAULT_MIN_RATE, confidence=0.95):
    """达到目标置信度所需的重复次数：n >= ln(1-conf)/ln(1-p0)。"""
    return math.ceil(math.log(1 - confidence) / math.log(1 - min_rate))


def summarize_by_test(records):
    """按测试聚合失败数，返回 {test_id: {"failures": f, "n": n}}。"""
    agg = {}
    for rec in records:
        for r in rec["results"]:
            slot = agg.setdefault(r["test_id"], {"failures": 0, "n": 0})
            slot["n"] += 1
            if r["status"] != "passed":
                slot["failures"] += 1
    return agg


def order_dependence(records, diff_threshold=0.3, min_shuffled_fail_rate=0.1):
    """对比固定顺序与打乱顺序两种模式的失败率，识别顺序相关失败。

    规则（阈值见 README）：
      - 固定顺序 0 失败而打乱顺序失败率 >= min_shuffled_fail_rate（或反向）
        -> "顺序相关"
      - 两模式失败率之差 >= diff_threshold -> "疑似顺序相关"
    返回 {test_id: {...}}。
    """
    per_mode = {}
    for rec in records:
        mode = rec["mode"]
        for r in rec["results"]:
            slot = per_mode.setdefault(r["test_id"], {}).setdefault(
                mode, {"failures": 0, "n": 0})
            slot["n"] += 1
            if r["status"] != "passed":
                slot["failures"] += 1

    out = {}
    for test_id, modes in per_mode.items():
        fixed = modes.get("fixed", {"failures": 0, "n": 0})
        shuffled = modes.get("shuffled", {"failures": 0, "n": 0})
        fr = fixed["failures"] / fixed["n"] if fixed["n"] else None
        sr = shuffled["failures"] / shuffled["n"] if shuffled["n"] else None
        flag = ""
        if fr is not None and sr is not None:
            diff = sr - fr
            if (fr == 0 and sr >= min_shuffled_fail_rate) or \
               (sr == 0 and fr >= min_shuffled_fail_rate):
                flag = "顺序相关"
            elif abs(diff) >= diff_threshold:
                flag = "疑似顺序相关"
        out[test_id] = {
            "fixed": fixed, "shuffled": shuffled,
            "fixed_rate": fr, "shuffled_rate": sr, "flag": flag,
        }
    return out
