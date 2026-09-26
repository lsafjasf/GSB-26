"""旧行为：固定切分 + 单一 key=value 输出格式。

旧管道把每行按空白切分为 时间 级别 其余为消息，输出
``ts=<...> level=<...> msg=<...>``。该模式保持可用（默认或 --legacy），
新接入方应迁移到声明式规则，见 README 的迁移说明。
"""
from __future__ import annotations

from typing import TextIO


def run_legacy(lines, out: TextIO, err: TextIO) -> int:
    """返回失败行数。空行视为失败行并单独报告，不中断整批。"""
    failed = 0
    for line_no, line in enumerate(lines, 1):
        parts = line.split(None, 2)
        if len(parts) < 3:
            failed += 1
            err.write(f"line {line_no}: 无法按旧格式切分（需要 时间 级别 消息）: "
                      f"{line.rstrip()!r}\n")
            continue
        ts, level, msg = parts
        out.write(f"ts={ts} level={level} msg={msg.rstrip()}\n")
    return failed
