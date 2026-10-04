#!/usr/bin/env bash
# 一键回归：边界用例 + 对拍 + 并行度基准
set -euo pipefail
cd "$(dirname "$0")"

echo "### 1/3 边界用例"
python3 test_qengine.py -v

echo; echo "### 2/3 对拍（串行基准 vs 各并行度）"
python3 gen_golden.py
python3 verify_golden.py

echo; echo "### 3/3 并行度耗时基准"
if [ ! -f data/bench/manifest.txt ]; then
  python3 gen_bench.py
fi
python3 bench.py "$@"
