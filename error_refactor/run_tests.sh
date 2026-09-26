#!/usr/bin/env bash
# 运行全部回归测试（对拍 + 原因链），仅依赖 Python 3 标准库
cd "$(dirname "$0")"
exec python3 -m unittest discover -s tests -v
