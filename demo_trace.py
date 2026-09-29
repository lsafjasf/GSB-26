"""追踪功能演示：对代表性输入输出命中规则来源追踪，并导出可读报告。

用法:
    python3 demo_trace.py            # 打印追踪并写出 trace_report.md
    python3 demo_trace.py -o out.md  # 指定报告输出路径
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import refactored
from rule_engine import export_report

# 代表性用例：覆盖优先级跳过（R05>R06、R08>R09）、普通命中与默认分支
CASES = [
    ("member", 1500, 40, "overseas", False),  # R05 命中，R06 被优先级跳过
    ("vip", 500, 20, "remote", False),       # R08 命中，R09 被优先级跳过
    ("member", 100, 12, "local", True),      # R01 易碎超重（运费为 callable）
    ("guest", 100, 3, "local", False),       # 默认分支 DEFAULT
]


def main():
    out = sys.argv[sys.argv.index("-o") + 1] if "-o" in sys.argv else "trace_report.md"
    traces = []
    for case in CASES:
        result, trace = refactored.explain(*case)
        assert result == refactored.decide(*case)  # 追踪结论 == 实际结论
        print(trace.render())
        print(f"实际判定结果: {result}")
        print("-" * 60)
        traces.append(trace)
    path = export_report(traces, out)
    print(f"报告已导出: {path}")


if __name__ == "__main__":
    main()
