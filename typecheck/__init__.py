"""A small constraint-based type checker (library + self tests)."""
from .ast_nodes import (Binding, BoolLit, Call, If, IntLit, Lam, Let, LetRec,
                        Node, Pos, Program, StrLit, Var)
from .checker import CheckResult, Checker, check_program, parse_annotation
from .errors import Diagnostic, TypeCheckError
from .types import (BOOL, INT, STR, QVar, Scheme, TCon, TFun, TVar,
                    format_type)

__all__ = [
    "Binding", "BoolLit", "Call", "If", "IntLit", "Lam", "Let", "LetRec",
    "Node", "Pos", "Program", "StrLit", "Var",
    "CheckResult", "Checker", "check_program", "parse_annotation",
    "Diagnostic", "TypeCheckError",
    "BOOL", "INT", "STR", "QVar", "Scheme", "TCon", "TFun", "TVar",
    "format_type",
]
