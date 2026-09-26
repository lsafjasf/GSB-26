#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
echo "== 单元测试 =="
python3 -m unittest discover -s tests -v
echo "== 评估（混淆矩阵 + 阈值扫描） =="
python3 evaluate.py
echo "== 吞吐基准 =="
python3 bench.py
echo "== 完成，结果见 results/ =="
