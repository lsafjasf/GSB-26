"""docextract：源码注释文档提取库（仅标准库）。"""

from .extractor import extract_file, extract_source
from .docparse import parse_docstring

__version__ = "0.1.0"
__all__ = ["extract_file", "extract_source", "parse_docstring"]
