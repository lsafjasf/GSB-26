"""规则完整性检查与未覆盖字符策略的可复跑验证。

用法: python3 verify_rules.py [config_path]

输出:
  1. 配置校验结论（问题列表，空 = 通过）
  2. 完整性检查：未被任何规则覆盖的字符类别（写入 coverage_report.txt）
  3. 同一批数据在三种未覆盖策略下的排序结果对比
  4. explain() 逐字符解释样例
  5. 复算验证：完整性检查连跑两次，digest 必须一致
"""

import json
import sys

from localesort import LocaleSorter, UncoveredCharError, validate_config

CONFIG_PATH = sys.argv[1] if len(sys.argv) > 1 else "sort_rules.json"

# 同一批数据：覆盖字符 + 各类未覆盖字符（假名/emoji/货币/生僻字/未映射变音）
DATA = [
    "上海", "apple", "ångström", "Ábc", "abc", "北京",
    "文件2", "文件10", "龘", "€9", "Ω-3", "あいう", "🙂ok", "item2", "item10",
]


def main():
    with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
        config = json.load(fh)

    print("=" * 68)
    print("1. 配置校验 (%s)" % CONFIG_PATH)
    print("=" * 68)
    issues = validate_config(config)
    if issues:
        for msg in issues:
            print("  [问题] %s" % msg)
    else:
        print("  通过：0 个问题（表间无冲突、位次无重复、枚举值合法）")

    sorter = LocaleSorter(config)

    print()
    print("=" * 68)
    print("2. 规则完整性检查：未被任何规则覆盖的字符类别")
    print("=" * 68)
    report = sorter.coverage_check()
    print("  Unicode 数据版本: %s, 配置覆盖字符数: %d"
          % (report["unidata_version"], report["covered_chars"]))
    for entry in report["uncovered_categories"]:
        samples = " ".join("U+%04X %s" % (ord(c), c) for c in entry["samples"])
        print("  %s %-22s 未覆盖 %6d 字符  样例: %s"
              % (entry["category"], entry["name"], entry["uncovered"], samples))
    with open("coverage_report.txt", "w", encoding="utf-8") as fh:
        fh.write(sorter.format_coverage_report(report))
    print("  digest: %s" % report["digest"])
    print("  （完整报告已写入 coverage_report.txt）")

    print()
    print("=" * 68)
    print("3. 同一批数据在三种未覆盖策略下的排序结果")
    print("=" * 68)
    for policy in ("codepoint", "fold", "error"):
        cfg = dict(config)
        cfg["uncovered_policy"] = policy
        s = LocaleSorter(cfg)
        try:
            got = s.sort(DATA)
            print("  [%-9s] %s" % (policy, " < ".join(got)))
        except UncoveredCharError as exc:
            print("  [%-9s] 拒绝排序: %s" % (policy, exc))

    print()
    print("=" * 68)
    print("4. explain(): 'å文a9' 在各策略下的逐字符解释")
    print("=" * 68)
    for policy in ("codepoint", "fold"):
        cfg = dict(config)
        cfg["uncovered_policy"] = policy
        s = LocaleSorter(cfg)
        print("  [%s]" % policy)
        for ch, entry in zip("å文a9", s.explain("å文a9")):
            print("    %r -> primary=%s secondary=%s tertiary=%s (%s)"
                  % (ch, entry["primary"], entry["secondary"],
                     entry["tertiary"], entry["source"]))

    print()
    print("=" * 68)
    print("5. 复算验证")
    print("=" * 68)
    again = sorter.coverage_check()
    assert again == report, "完整性检查结果不可复算！"
    print("  完整性检查连跑两次 digest 一致: %s" % (again["digest"] == report["digest"]))
    cfg = dict(config)
    cfg["uncovered_policy"] = "fold"
    s = LocaleSorter(cfg)
    assert s.sort(DATA) == s.sort(list(DATA))
    print("  fold 策略下重复排序结果一致: True")


if __name__ == "__main__":
    main()
