"""规则覆盖度分析入口。

用法：
    python3 analyze_coverage.py            # 分析并打印报告，写 coverage_report.txt
    python3 analyze_coverage.py --check    # 同上，且对拍失败时退出码非零

分析对象：refactored_decision.RULES（15 条业务规则）
数据集：  datasets 模块的边界网格（13824 例）+ 确定性模糊（20000 例）
输出：    coverage_report.txt —— 内容确定性（无时间戳/随机源），可复算：
          同一代码版本重复运行，报告逐字节一致。
"""

import sys

import datasets
import refactored_decision
from rule_coverage import analyze, infer_domains, render_report

REPORT_PATH = "coverage_report.txt"


def build_cases():
    grid = list(datasets.grid_cases())
    fuzz = list(datasets.fuzz_cases())
    name = "网格 %d 例 + 模糊 %d 例（seed=%d）" % (
        len(grid), len(fuzz), datasets.FUZZ_SEED)
    return grid + fuzz, name


def main(check=False):
    cases, name = build_cases()
    report = analyze(list(refactored_decision.RULES), cases,
                     domains=infer_domains(cases), dataset_name=name)
    text = render_report(report)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(text)
    sys.stdout.write(text)
    print("报告已写入 %s" % REPORT_PATH)
    if check and report.diff_mismatches:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(check="--check" in sys.argv[1:]))
