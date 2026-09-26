"""tplcheck - template placeholder validation & type checking.

Public API:

* :func:`parse`     - parse a template into an AST
* :func:`validate`  - validate one template, extract its parameter signature
* :func:`compare`   - compare language variants of the same template
* :func:`render`    - strict reference renderer (for differential testing)
"""

from .compare import CompareReport, compare
from .errors import Diagnostic, Position, RenderError, TemplateSyntaxError
from .parser import Each, If, Placeholder, Text, parse
from .renderer import render
from .validator import ParamInfo, ValidationResult, validate

__all__ = [
    "CompareReport",
    "Diagnostic",
    "Each",
    "If",
    "ParamInfo",
    "Placeholder",
    "Position",
    "RenderError",
    "TemplateSyntaxError",
    "Text",
    "ValidationResult",
    "compare",
    "parse",
    "render",
    "validate",
]

__version__ = "0.1.0"
