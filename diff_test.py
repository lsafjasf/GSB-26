"""对拍脚本：numfmt（Decimal 实现） vs reference_impl（Fraction 精确有理数实现）。

用法：python3 diff_test.py [迭代次数] [随机种子]
默认 20000 组随机用例：随机数值 × 随机语言 × 随机精度/舍入/科学计数法配置，
两边输出字符串必须完全一致；另附定向边界用例（半值、阈值切换、负零等）。
"""
import json
import random
import sys

import numfmt
from reference_impl import format_reference

CFG = json.load(open("locales.json", encoding="utf-8"))
LOCALE_CFGS = list(CFG["locales"].values())


def random_value(rng):
    kind = rng.randrange(8)
    if kind == 0:      # 普通小数
        return f"{rng.randrange(10**6)}.{rng.randrange(10**6):06d}"
    if kind == 1:      # 半值及附近（精确 tie 判定）
        base = rng.randrange(1000)
        tail = rng.choice(["5", "05", "005", "5000000001", "4999999999",
                           "25", "75", "125", "375"])
        return f"{base}.{tail}"
    if kind == 2:      # 超长小数
        return f"{rng.randrange(100)}." + "".join(rng.choice("0123456789")
                                                  for _ in range(rng.randrange(20, 120)))
    if kind == 3:      # 科学计数法输入，极小/极大
        return f"{rng.randrange(1, 10**8)}e{rng.randrange(-80, 80)}"
    if kind == 4:      # 零与负零
        return rng.choice(["0", "-0", "0.000", "-0.000", "-0.0000001"])
    if kind == 5:      # 大整数
        return str(rng.randrange(10**rng.randrange(1, 40)))
    if kind == 6:      # 9 的进位链
        return "9" * rng.randrange(1, 15) + "." + "9" * rng.randrange(0, 15)
    # 小数值
    return "0." + "0" * rng.randrange(0, 10) + str(rng.randrange(1, 10**6))


def random_spec(rng):
    spec = {
        "mode": rng.choice(["places", "significant"]),
        "places": rng.randrange(0, 12),
        "sig_digits": rng.randrange(1, 12),
        "rounding": rng.choice(["half_up", "half_even"]),
        "group": rng.random() < 0.8,
        "show_plus": rng.random() < 0.3,
        "negative_style": rng.choice(["sign", "parens"]),
        "keep_negative_zero": rng.random() < 0.5,
        "sci_high": rng.choice([None, None, 6, 7, 10, 21]),
        "sci_low": rng.choice([None, None, -4, -5, -7]),
        "sci_mantissa_digits": rng.choice([None, None, 3, 5]),
        "currency": rng.choice([None, None,
                                {"symbol": "$", "position": "prefix"},
                                {"symbol": "€", "position": "suffix", "space": True},
                                {"symbol": "¥", "position": "prefix"}]),
    }
    return spec


def directed_cases():
    values = ["0", "-0", "-0.000", "0.5", "1.5", "2.5", "-2.5", "2.675",
              "0.125", "0.135", "9999999.9999995", "9999999.9999994",
              "0.0001", "0.00001", "1e-300", "-1e-300", "1e300", "-1e300",
              "0." + "9" * 500, "1." + "3" * 500, "123456789012345678901234567890"]
    specs = [
        {"places": 2, "rounding": "half_up"},
        {"places": 2, "rounding": "half_even"},
        {"places": 0, "rounding": "half_even"},
        {"mode": "significant", "sig_digits": 6, "rounding": "half_up"},
        {"places": 6, "rounding": "half_up", "sci_high": 7, "sci_low": -5},
        {"places": 4, "rounding": "half_even", "sci_high": 10, "sci_low": -7,
         "currency": {"symbol": "$", "position": "prefix"},
         "negative_style": "parens", "show_plus": True},
    ]
    for v in values:
        for s in specs:
            for loc in LOCALE_CFGS:
                yield v, s, loc


def run(iterations, seed):
    rng = random.Random(seed)
    cases = list(directed_cases())
    cases += [(random_value(rng), random_spec(rng), rng.choice(LOCALE_CFGS))
              for _ in range(iterations)]
    mismatches = 0
    for i, (value, spec, loc) in enumerate(cases):
        v = ("-" if rng.random() < 0.4 else "") + value if value[0] != "-" else value
        got = numfmt.format_number(v, spec, loc)
        want = format_reference(v, numfmt.normalize_spec(spec).__dict__ | {
            "currency": (spec["currency"] if isinstance(spec.get("currency"), dict)
                         else (spec.get("currency").__dict__ if spec.get("currency") else None))},
            numfmt.normalize_locale(loc).__dict__)
        if got != want:
            mismatches += 1
            print(f"MISMATCH #{i}: value={v!r}\n  spec={spec}\n  locale={loc}"
                  f"\n  numfmt   : {got!r}\n  reference: {want!r}")
            if mismatches >= 10:
                break
    total = len(cases)
    if mismatches:
        print(f"\n失败：{mismatches} 处不一致（共检查 {total} 例）")
        return 1
    print(f"通过：{total} 例（含 {len(list(directed_cases()))} 例定向边界用例），"
          f"numfmt 与 Fraction 参考实现输出完全一致。")
    return 0


if __name__ == "__main__":
    iters = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 20260927
    sys.exit(run(iters, seed))
