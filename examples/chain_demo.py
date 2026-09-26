"""原因链追溯输出样例：python3 examples/chain_demo.py"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from refactored import service
from refactored.errors import AppError, format_chain


def boom_dial(dsn):
    raise ConnectionRefusedError(111, "Connection refused")


try:
    service.load_profile("42",
                         transport=lambda url, t: b"{}",
                         dial=boom_dial,
                         backend=lambda key: b"x")
except AppError as exc:
    print("=== 原因链追溯 ===")
    print(format_chain(exc))
    print()
    print("=== 结构化上报（可进日志系统/告警） ===")
    for err in (exc, exc.__cause__):
        print(err.to_dict())
