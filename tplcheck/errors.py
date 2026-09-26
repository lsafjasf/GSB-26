"""Error types and diagnostics for tplcheck."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Position:
    """1-based line/column position inside a template source."""

    line: int
    column: int
    offset: int

    def __str__(self) -> str:
        return f"{self.line}:{self.column}"


@dataclass
class Diagnostic:
    """A single validation / comparison finding.

    ``kind`` is a stable machine-readable code, e.g. ``TYPE_CONFLICT``.
    """

    kind: str
    message: str
    pos: Optional[Position] = None
    lang: Optional[str] = None

    def __str__(self) -> str:
        where = ""
        if self.lang is not None:
            where += f"[{self.lang}] "
        if self.pos is not None:
            where += f"{self.pos.line}:{self.pos.column} "
        return f"{where}{self.kind}: {self.message}"


class TemplateSyntaxError(Exception):
    """Raised when a template cannot be parsed."""

    def __init__(self, message: str, pos: Optional[Position] = None):
        self.pos = pos
        suffix = f" (at {pos})" if pos else ""
        super().__init__(message + suffix)
        self.message = message


class RenderError(Exception):
    """Raised when rendering fails (missing arg, bad type, bad scope...)."""
