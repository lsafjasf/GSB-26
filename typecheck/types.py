"""Type representations for the constraint based type checker."""
from __future__ import annotations


class Type:
    __slots__ = ()


class TCon(Type):
    """Nullary type constant: int, bool, str."""

    __slots__ = ("name",)

    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return f"TCon({self.name!r})"


INT = TCon("int")
BOOL = TCon("bool")
STR = TCon("str")

CONSTANTS = {"int": INT, "bool": BOOL, "str": STR}


class TFun(Type):
    __slots__ = ("arg", "ret")

    def __init__(self, arg, ret):
        self.arg = arg
        self.ret = ret

    def __repr__(self):
        return f"TFun({self.arg!r}, {self.ret!r})"


class TVar(Type):
    """Unification variable. ``instance`` is the union-find link and
    ``bound_by`` records the constraint that bound it (for error chains)."""

    __slots__ = ("vid", "hint", "instance", "bound_by")

    def __init__(self, vid, hint=""):
        self.vid = vid
        self.hint = hint
        self.instance = None
        self.bound_by = None

    def __repr__(self):
        return f"TVar({self.vid})"


class QVar(Type):
    """Quantified (generic placeholder) variable inside a Scheme."""

    __slots__ = ("qid", "hint")

    def __init__(self, qid, hint=""):
        self.qid = qid
        self.hint = hint

    def __repr__(self):
        return f"QVar({self.qid})"


class Scheme:
    """A polymorphic type: ``qvars`` universally quantified over ``type``."""

    __slots__ = ("qvars", "type", "closed")

    def __init__(self, qvars, type_, closed=False):
        self.qvars = qvars
        self.type = type_
        # closed=True means the type contains no free unification variables
        # at all, so it can be skipped when scanning an environment.
        self.closed = closed


def prune(t):
    """Follow union-find links to the representative."""
    while isinstance(t, TVar) and t.instance is not None:
        t = t.instance
    return t


def occurs(var, t):
    t = prune(t)
    if t is var:
        return True
    if isinstance(t, TFun):
        return occurs(var, t.arg) or occurs(var, t.ret)
    return False


def free_tvars(t, out=None):
    if out is None:
        out = []
    t = prune(t)
    if isinstance(t, TVar):
        if all(t is not v for v in out):
            out.append(t)
    elif isinstance(t, TFun):
        free_tvars(t.arg, out)
        free_tvars(t.ret, out)
    return out


def format_type(t):
    """Human readable rendering; free variables become 'a, 'b, ..."""
    names = {}
    counter = [0]

    def name_for(key):
        if key not in names:
            n = counter[0]
            counter[0] += 1
            names[key] = "'" + chr(ord("a") + n) if n < 26 else f"'t{n}"
        return names[key]

    def go(t, nested):
        t = prune(t)
        if isinstance(t, TCon):
            return t.name
        if isinstance(t, TVar):
            return name_for(("v", t.vid))
        if isinstance(t, QVar):
            return name_for(("q", t.qid))
        if isinstance(t, TFun):
            s = f"{go(t.arg, True)} -> {go(t.ret, False)}"
            return f"({s})" if nested else s
        raise TypeError(t)

    return go(t, False)
