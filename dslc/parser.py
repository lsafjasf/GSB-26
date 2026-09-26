"""Recursive-descent parser: tokens -> Config AST."""

from .ast_nodes import (BOOL, INT, STR, Bin, Branch, Case, Config, Lit, Not,
                        Param, Step, TypeSpec, Unresolved)
from .errors import DSLError

CMP_OPS = ("==", "!=", "<=", ">=", "<", ">")


class Parser:
    def __init__(self, tokens):
        self.toks = tokens
        self.i = 0

    def peek(self):
        return self.toks[self.i]

    def advance(self):
        t = self.toks[self.i]
        if t.kind != "EOF":
            self.i += 1
        return t

    def error(self, msg, tok=None):
        t = tok or self.peek()
        raise DSLError(msg, t.line, t.col)

    def expect_punc(self, p):
        t = self.peek()
        if t.kind != "PUNC" or t.value != p:
            self.error("expected {!r}, got {!r}".format(p, t.value))
        return self.advance()

    def accept_punc(self, p):
        t = self.peek()
        if t.kind == "PUNC" and t.value == p:
            return self.advance()
        return None

    def expect_kw(self, kw):
        t = self.peek()
        if t.kind != "KW" or t.value != kw:
            self.error("expected {!r}, got {!r}".format(kw, t.value))
        return self.advance()

    def expect_ident(self):
        t = self.peek()
        if t.kind != "IDENT":
            self.error("expected identifier, got {!r}".format(t.value))
        return self.advance()


def parse_config(tokens):
    p = Parser(tokens)
    params, steps = [], []
    while p.peek().kind != "EOF":
        t = p.peek()
        if t.kind == "KW" and t.value == "param":
            params.append(parse_param(p))
        elif t.kind == "KW" and t.value == "step":
            steps.append(parse_step(p))
        else:
            p.error("expected 'param' or 'step', got {!r}".format(t.value))
    return Config(params, steps)


def parse_param(p):
    t = p.expect_kw("param")
    name = p.expect_ident()
    p.expect_punc(":")
    typ = parse_type(p)
    default = None
    if p.accept_punc("="):
        default = parse_value(p)
    return Param(name.value, typ, default, t.line, t.col)


def parse_type(p):
    tok = p.expect_ident()
    if tok.value in ("str", "int", "bool"):
        return TypeSpec(tok.value)
    if tok.value == "enum":
        p.expect_punc("(")
        vals = [p.expect_ident().value]
        while p.accept_punc(","):
            vals.append(p.expect_ident().value)
        p.expect_punc(")")
        if len(set(vals)) != len(vals):
            raise DSLError("duplicate enum value", tok.line, tok.col)
        return TypeSpec("enum", tuple(vals))
    raise DSLError("unknown type {!r}".format(tok.value), tok.line, tok.col)


def parse_value(p):
    t = p.peek()
    if t.kind == "STRING":
        p.advance()
        return Lit(t.value, STR, t.line, t.col)
    if t.kind == "INT":
        p.advance()
        return Lit(t.value, INT, t.line, t.col)
    if t.kind == "KW" and t.value in ("true", "false"):
        p.advance()
        return Lit(t.value == "true", BOOL, t.line, t.col)
    if t.kind == "IDENT":
        p.advance()
        return Unresolved(t.value, t.line, t.col)
    p.error("expected a value, got {!r}".format(t.value))


def parse_step(p):
    t = p.expect_kw("step")
    name = p.expect_ident()
    p.expect_punc("{")
    needs, args = [], []
    when = run = branch = None
    while True:
        tok = p.peek()
        if tok.kind == "PUNC" and tok.value == "}":
            break
        if tok.kind != "KW":
            p.error("expected step body item, got {!r}".format(tok.value))
        if tok.value == "needs":
            p.advance()
            p.expect_punc(":")
            needs.append(need_item(p))
            while p.accept_punc(","):
                needs.append(need_item(p))
        elif tok.value == "when":
            if when is not None:
                p.error("duplicate 'when' in step {!r}".format(name.value))
            p.advance()
            p.expect_punc(":")
            when = parse_expr(p)
        elif tok.value == "args":
            p.advance()
            p.expect_punc(":")
            args.append(arg_item(p))
            while p.accept_punc(","):
                args.append(arg_item(p))
        elif tok.value == "run":
            if run is not None:
                p.error("duplicate 'run' in step {!r}".format(name.value))
            p.advance()
            p.expect_punc(":")
            s = p.peek()
            if s.kind != "STRING":
                p.error("expected string after 'run:'")
            p.advance()
            run = s.value
        elif tok.value == "branch":
            if branch is not None:
                p.error("duplicate 'branch' in step {!r}".format(name.value))
            branch = parse_branch(p)
        else:
            p.error("unexpected {!r} in step body".format(tok.value))
    p.expect_punc("}")
    return Step(name.value, needs, when, args, run, branch, t.line, t.col)


def need_item(p):
    t = p.expect_ident()
    return (t.value, t.line, t.col)


def arg_item(p):
    t = p.expect_ident()
    p.expect_punc("=")
    v = parse_value(p)
    return (t.value, v, t.line, t.col)


def parse_branch(p):
    t = p.expect_kw("branch")
    p.expect_kw("on")
    on = parse_expr(p)
    p.expect_punc("{")
    cases = []
    else_target = None
    has_else = False
    while True:
        tok = p.peek()
        if tok.kind == "PUNC" and tok.value == "}":
            break
        if tok.kind == "KW" and tok.value == "case":
            ct = p.advance()
            label = parse_value(p)
            p.expect_punc("->")
            cases.append(Case(label, parse_target(p), ct.line, ct.col))
        elif tok.kind == "KW" and tok.value == "else":
            et = p.advance()
            if has_else:
                raise DSLError("duplicate 'else' in branch", et.line, et.col)
            has_else = True
            p.expect_punc("->")
            else_target = parse_target(p)
        else:
            p.error("expected 'case' or 'else', got {!r}".format(tok.value))
    p.expect_punc("}")
    return Branch(on, cases, else_target, t.line, t.col, has_else)


def parse_target(p):
    t = p.peek()
    if t.kind == "KW" and t.value == "skip":
        p.advance()
        return None
    if t.kind == "IDENT":
        p.advance()
        return (t.value, t.line, t.col)
    p.error("expected step name or 'skip', got {!r}".format(t.value))


def parse_expr(p):
    return parse_or(p)


def parse_or(p):
    left = parse_and(p)
    while True:
        t = p.peek()
        if t.kind == "KW" and t.value == "or":
            p.advance()
            left = Bin("or", left, parse_and(p), t.line, t.col)
        else:
            return left


def parse_and(p):
    left = parse_not(p)
    while True:
        t = p.peek()
        if t.kind == "KW" and t.value == "and":
            p.advance()
            left = Bin("and", left, parse_not(p), t.line, t.col)
        else:
            return left


def parse_not(p):
    t = p.peek()
    if t.kind == "KW" and t.value == "not":
        p.advance()
        return Not(parse_not(p), t.line, t.col)
    return parse_cmp(p)


def parse_cmp(p):
    left = parse_primary(p)
    t = p.peek()
    if t.kind == "PUNC" and t.value in CMP_OPS:
        p.advance()
        return Bin(t.value, left, parse_primary(p), t.line, t.col)
    return left


def parse_primary(p):
    t = p.peek()
    if t.kind == "PUNC" and t.value == "(":
        p.advance()
        e = parse_expr(p)
        p.expect_punc(")")
        return e
    return parse_value(p)
