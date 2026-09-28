#!/usr/bin/env bash
# 一键复跑：全部性质测试 + 资源治理测试 + 缺陷复现 + 二维内存基准。
# 用法：./run_verification.sh            # 结果同时落盘 verification_output.txt
set -euo pipefail
cd "$(dirname "$0")"

LOG=verification_output.txt
: > "$LOG"

run() {
  echo "### $*" | tee -a "$LOG"
  "$@" 2>&1 | tee -a "$LOG"
  echo | tee -a "$LOG"
}

run python3 -m unittest test_external_sort -v
run python3 -m unittest test_resource_governance -v
run python3 -m unittest test_reproduce -v
run python3 memory_benchmark.py --csv memory_peak_2d.csv

echo "全部复跑完成，完整输出见 $LOG，二维明细见 memory_peak_2d.csv"
