#!/usr/bin/env python3
"""Error recovery demo: the lexer reports each problem with its position,
skips illegal characters, and keeps tokenizing to the end of the input."""
from lexer import Lexer, load_config

SAMPLE_PATH = "examples/error_sample.src"


def main():
    lexer = Lexer(load_config("rules.json"))
    with open(SAMPLE_PATH, "r", encoding="utf-8") as fh:
        text = fh.read()
    tokens, errors = lexer.tokenize(text)

    print(f"== source: {SAMPLE_PATH} ==")
    print(text)
    print(f"== tokens ({len(tokens)}) ==")
    for tok in tokens:
        snippet = tok.text if len(tok.text) <= 32 else tok.text[:29] + "..."
        print(f"  {tok.start_line}:{tok.start_col}-"
              f"{tok.end_line}:{tok.end_col}\t{tok.type}\t{snippet!r}")
    print(f"== errors ({len(errors)}) ==")
    for err in errors:
        print(f"  {err.line}:{err.col}\t{err.message}")
    print("\nLexer did not abort: tokens and errors were both returned.")


if __name__ == "__main__":
    main()
