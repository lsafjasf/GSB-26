"""Error-recovery demo: illegal characters and unterminated constructs
are reported with positions, skipped, and tokenization continues."""

import json
from lexer_lib.lexer import Lexer

SRC = '''int main() {
    float pi = 3.14;
    char *s = "hello \\"world;   // unterminated string above
    int x = y @ z # 42;          // illegal chars @ and #
    /* outer /* inner */ still comment
    return 0;                    // <- inside unterminated comment
'''

lexer = Lexer(json.load(open("rules.json", encoding="utf-8")))
tokens, errors = lexer.tokenize(SRC)

print("=== TOKENS ===")
for t in tokens:
    print("%-8s %-28r %d:%d-%d:%d" % (t.type, t.text, t.start_line,
                                      t.start_col, t.end_line, t.end_col))
print("=== ERRORS (%d) ===" % len(errors))
for e in errors:
    print("%-20s at %d:%d  %r" % (e.message, e.line, e.col, e.text[:40]))
