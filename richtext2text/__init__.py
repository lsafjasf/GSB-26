"""richtext2text: convert HTML rich-text markup to readable plain text.

Only the Python standard library is used.
"""

from .converter import Config, DEFAULT_CONFIG, convert, convert_file

__version__ = "1.0.0"
__all__ = ["Config", "DEFAULT_CONFIG", "convert", "convert_file",
           "__version__"]
