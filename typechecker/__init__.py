"""小型类型检查器：约束生成 + 合一求解，标准库实现。"""
from .infer import CheckResult, Checker, Diagnostic
from .parser import ParseError, parse_program

__all__ = ["check_source", "CheckResult", "Checker", "Diagnostic",
           "ParseError", "parse_program"]


def check_source(src: str) -> CheckResult:
    """解析并检查一段 DSL 源码，返回诊断结果。"""
    program = parse_program(src)
    return Checker().check(program)
