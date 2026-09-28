#!/usr/bin/env bash
# 一键可复跑验证：单元/缺陷复现 -> 小规模逐条顺序核对 -> 三档规模耗时/内存/顺序校验。
# 产物写入 results/（含节点序/边序 SHA-256，可跨次运行比对）。
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p results

echo "### [1/3] unittest：4 缺陷复现 + 不变量 + 顺序/去重 + 十万链"
python3 -m unittest -v test_dfs.py 2>&1 | tee results/unittest.txt

echo
echo "### [2/3] verify.py：小图节点序/边序逐条核对（含自环/并行/无向/simple）"
python3 verify.py 2>&1 | tee results/verify_small.txt

echo
echo "### [3/3] benchmark.py：十万深链 + 十万/百万 + 百万/千万 耗时·内存·顺序"
python3 benchmark.py 2>&1 | tee results/benchmark.json

echo
echo "全部完成。规模结果 JSON：results/benchmark.json"
