"""Constraint solver: union-find unification with an occurs check, a
provenance journal (so type errors can show the constraint chain that
caused them) and a hard step budget (a defensive termination guarantee)."""
from dataclasses import dataclass

from .types import TCon, TFun, TVar, occurs, prune


@dataclass
class Constraint:
    cid: int
    left: object
    right: object
    reason: str
    node: object


class ConflictError(Exception):
    def __init__(self, constraint, left, right):
        super().__init__("type mismatch")
        self.constraint = constraint
        self.left = left
        self.right = right


class OccursError(Exception):
    def __init__(self, constraint, var, into):
        super().__init__("occurs check failed")
        self.constraint = constraint
        self.var = var
        self.into = into


class FuelExhaustedError(Exception):
    """Raised if the solver ever exceeds its step budget. Reaching this
    indicates a checker bug, not a valid or invalid program."""


class Unifier:
    def __init__(self, max_steps=2_000_000):
        self.constraints = []
        self.steps = 0
        self.max_steps = max_steps

    def unify(self, left, right, reason, node):
        c = Constraint(len(self.constraints), left, right, reason, node)
        self.constraints.append(c)
        self._unify(left, right, c)
        return c

    def _unify(self, a, b, c):
        self.steps += 1
        if self.steps > self.max_steps:
            raise FuelExhaustedError(
                f"solver exceeded its step budget ({self.max_steps})")
        pa, pb = prune(a), prune(b)
        if pa is pb:
            return
        if isinstance(pa, TVar):
            self._bind(pa, pb, c)
            return
        if isinstance(pb, TVar):
            self._bind(pb, pa, c)
            return
        if isinstance(pa, TCon) and isinstance(pb, TCon):
            if pa.name != pb.name:
                raise ConflictError(c, a, b)
            return
        if isinstance(pa, TFun) and isinstance(pb, TFun):
            self._unify(pa.arg, pb.arg, c)
            self._unify(pa.ret, pb.ret, c)
            return
        raise ConflictError(c, a, b)

    def _bind(self, var, t, c):
        if occurs(var, t):
            raise OccursError(c, var, t)
        var.instance = t
        var.bound_by = c

    def _provenance(self, t, seen, out):
        while isinstance(t, TVar) and t.instance is not None:
            if t.bound_by is not None and t.bound_by.cid not in seen:
                seen.add(t.bound_by.cid)
                out.append(t.bound_by)
            t = t.instance
        t = prune(t)
        if isinstance(t, TFun):
            self._provenance(t.arg, seen, out)
            self._provenance(t.ret, seen, out)

    def chain_for(self, left, right, failing):
        """Ordered list of constraints that produced the two conflicting
        types, ending with the constraint that failed."""
        seen = set()
        out = []
        self._provenance(left, seen, out)
        self._provenance(right, seen, out)
        out.append(failing)
        out.sort(key=lambda c: c.cid)
        return out
