"""dslc: a config DSL compiler (parse -> validate -> compile -> optimize)."""

from .compiler import compile_plan
from .errors import DSLError, DSLErrorList
from .interp import effectful_sequence, execute, traces_equivalent
from .lexer import tokenize
from .parser import parse_config
from .validate import validate

__version__ = "1.0.0"
