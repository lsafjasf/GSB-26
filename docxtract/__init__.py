"""docxtract：源码注释文档提取库（仅标准库）。

用法：
    from docxtract import extract, extract_file
    result = extract_file("my_module.py")
"""

from .core import extract, extract_file
from .docparse import parse_doc

__all__ = ["extract", "extract_file", "parse_doc"]
__version__ = "0.1.0"
