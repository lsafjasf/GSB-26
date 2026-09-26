"""Diagnostics: located type errors with expected/actual types and the
constraint chain that led to the conflict."""
from dataclasses import dataclass, field

from .types import format_type


@dataclass
class Diagnostic:
    kind: str  # 'conflict' | 'occurs' | 'unbound' | 'uninferred'
    pos: object
    message: str
    expected: str = ""
    actual: str = ""
    chain: list = field(default_factory=list)  # list[solver.Constraint]

    def render(self):
        lines = [f"error[{self.kind}] at {self.pos}: {self.message}"]
        if self.expected:
            lines.append(f"  expected: {self.expected}")
        if self.actual:
            lines.append(f"  actual:   {self.actual}")
        if self.chain:
            lines.append("  constraint chain:")
            for c in self.chain:
                lines.append(
                    f"    #{c.cid} at {c.node.pos}: {c.reason}: "
                    f"{format_type(c.left)} ~ {format_type(c.right)}")
        return "\n".join(lines)


class TypeCheckError(Exception):
    def __init__(self, diagnostic):
        super().__init__(diagnostic.message)
        self.diagnostic = diagnostic
