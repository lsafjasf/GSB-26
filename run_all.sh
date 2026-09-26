#!/usr/bin/env bash
# 一键运行：退化用例 + 随机对拍 + 性能基准
set -euo pipefail
cd "$(dirname "$0")"

echo "=== 1/3 退化用例集 ==="
python3 tests/test_degenerate.py

echo
echo "=== 2/3 随机对拍（3000 组）==="
python3 tests/crosscheck.py 3000 20260926

echo
echo "=== 3/3 性能基准（快速档；完整档去掉 --quick）==="
python3 tests/benchmark.py --quick

echo
echo "全部通过。完整基准: python3 tests/benchmark.py"
