"""Random template/argument generation shared by differential tests and bench."""

from __future__ import annotations

import datetime
import random
from typing import Dict, List, Optional, Tuple

from tplcheck.parser import Each, If
from tplcheck.validator import ValidationResult

TYPES = ["str", "int", "float", "bool", "date", "datetime"]
WORDS = ["Hello", "你好", "こんにちは", "Bonjour", "Hola", "Total:", "合计", ""]


class TemplateGenerator:
    """Generates random *valid* templates together with scope information."""

    def __init__(self, rng: random.Random, max_depth: int = 4):
        self.rng = rng
        self.max_depth = max_depth
        self.counter = 0

    def fresh_name(self, prefix: str = "p") -> str:
        self.counter += 1
        return f"{prefix}{self.counter}"

    def generate(self, min_nodes: int = 1, max_nodes: int = 12) -> str:
        parts, _ = self._block(0, [], self.rng.randint(min_nodes, max_nodes))
        return "".join(parts)

    def _block(
        self, depth: int, visible: List[Tuple[str, str]], budget: int
    ) -> Tuple[List[str], List[Tuple[str, str]]]:
        """visible: names (with types) usable at this point (params + loop vars)."""
        parts: List[str] = []
        params: List[Tuple[str, str]] = []
        for _ in range(budget):
            choice = self.rng.random()
            if choice < 0.25:
                parts.append(self.rng.choice(WORDS))
                if parts[-1] and self.rng.random() < 0.5:
                    parts.append(" ")
            elif choice < 0.65 or depth >= self.max_depth:
                # placeholder: reuse a visible name or create a fresh param
                if visible and self.rng.random() < 0.4:
                    name, typ = self.rng.choice(visible)
                else:
                    name, typ = self.fresh_name(), self.rng.choice(TYPES)
                    params.append((name, typ))
                opt = "?" if self.rng.random() < 0.15 else ""
                parts.append(f"{{{name}:{typ}{opt}}}")
            elif choice < 0.85:
                # if section over a fresh or reused bool
                bools = [n for n, t in visible if t == "bool"]
                if bools and self.rng.random() < 0.5:
                    cond = self.rng.choice(bools)
                else:
                    cond = self.fresh_name("flag")
                    params.append((cond, "bool"))
                inner, inner_params = self._block(
                    depth + 1, visible, self.rng.randint(1, 4)
                )
                params.extend(inner_params)
                parts.append(f"{{#if {cond}}}" + "".join(inner) + "{/if}")
            else:
                # each section over a fresh list param
                source = self.fresh_name("items")
                var = self.fresh_name("it")
                elem_type = self.rng.choice(TYPES + ["list"])
                params.append((source, "list"))
                inner_visible = visible + [(var, elem_type)]
                inner, inner_params = self._block(
                    depth + 1, inner_visible, self.rng.randint(1, 4)
                )
                params.extend(inner_params)
                if elem_type == "list":
                    # the loop var is itself a list: wrap an inner loop so the
                    # generated template exercises nested lists
                    inner_var = self.fresh_name("sub")
                    sub_type = self.rng.choice(TYPES)
                    body = (
                        f"{{#each {var} as {inner_var}}}"
                        f"{{{inner_var}:{sub_type}}}"
                        "{/each}"
                    )
                    parts.append(
                        f"{{#each {source} as {var}}}" + body + "{/each}"
                    )
                else:
                    parts.append(
                        f"{{#each {source} as {var}}}" + "".join(inner) + "{/each}"
                    )
        return parts, params


def sample_value(typ: str, rng: random.Random):
    if typ == "str":
        return rng.choice(["x", "hello", "世界", ""])
    if typ == "int":
        return rng.randint(-1000, 1000)
    if typ == "float":
        return round(rng.uniform(-1000, 1000), 3)
    if typ == "bool":
        return rng.choice([True, False])
    if typ == "date":
        return datetime.date(2026, rng.randint(1, 12), rng.randint(1, 28))
    if typ == "datetime":
        return datetime.datetime(
            2026, rng.randint(1, 12), rng.randint(1, 28), rng.randint(0, 23), 0
        )
    return rng.choice([1, "s", True, 2.5])  # any


def gen_args(result: ValidationResult, rng: random.Random) -> Dict:
    """Generate arguments that satisfy the validated signature."""
    # map each-source name -> (loop var, element type)
    elem: Dict[str, Tuple[Optional[str], str]] = {}

    def walk(nodes):
        for node in nodes:
            if isinstance(node, Each):
                elem[node.source] = (
                    node.var,
                    result.loop_var_types.get(node.var, "any"),
                )
                walk(node.body)
            elif isinstance(node, If):
                walk(node.body)

    walk(result.ast)

    def value_of(typ: str, name: Optional[str]):
        if typ == "list":
            var, et = elem.get(name, (None, "any"))
            return [value_of(et, var) for _ in range(rng.randint(0, 3))]
        return sample_value(typ, rng)

    args: Dict = {}
    for name, info in result.signature.items():
        if info.optional and rng.random() < 0.3:
            continue  # omit optional args entirely
        args[name] = value_of(info.type, name)
    return args
