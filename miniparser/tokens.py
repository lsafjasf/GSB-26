"""基础数据结构：位置、词法单元、错误信息。仅使用标准库。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Pos:
    """源码位置。offset 为 0 基字符偏移，line/col 为 1 基。"""
    offset: int
    line: int
    col: int

    def __str__(self):
        return f"{self.line}:{self.col}"


@dataclass(frozen=True)
class Token:
    type: str
    value: str
    pos: Pos


@dataclass(frozen=True)
class ErrorInfo:
    """一条解析错误：位置 + 期望内容 + 实际内容。"""
    pos: Pos
    expected: str
    actual: str
    phase: str = "parse"  # "lex" 或 "parse"

    def __str__(self):
        return f"{self.pos}: [{self.phase}] 期望 {self.expected}，实际 {self.actual}"
