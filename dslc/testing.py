"""随机 DSL 配置生成器：对拍测试与性能基准共用。

生成的配置始终满足静态约束（参数有默认值、分支覆盖完整），
但刻意包含随机空 case，以触发空分支消除优化。
"""

import random

PREAMBLE = (
    "param flag: bool = true\n"
    "param mode: enum(a, b, c) = a\n"
    "param count: int = 1\n"
)


def random_config(rng, n_steps):
    lines = [PREAMBLE]
    for j in range(n_steps):
        parts = [f"step s{j} {{"]
        if j > 0:
            k = rng.randint(0, min(2, j))
            if k:
                deps = rng.sample(range(j), k)
                parts.append("  needs: " + ", ".join(f"s{d}" for d in deps) + ";")
        if rng.random() < 0.4:
            parts.append("  args: { count: $count, tag: \"x\" }")
        if j < n_steps - 2 and rng.random() < 0.25:
            cond = "flag" if rng.random() < 0.5 else "mode"
            parts.append(f"  branch on ${cond} {{")
            later = list(range(j + 1, min(n_steps, j + 8)))
            rng.shuffle(later)
            cursor = 0
            labels = ("true", "false") if cond == "flag" else ("a", "b", "c")
            for label in labels:
                take = 0 if rng.random() < 0.3 else rng.randint(1, 2)
                members = later[cursor:cursor + take]
                cursor += take
                body = ", ".join(f"s{m}" for m in members)
                parts.append(f"    {label}: {body};")
            parts.append("  }")
        parts.append("}")
        lines.append("\n".join(parts))
    return "\n".join(lines) + "\n"


def random_inputs(rng):
    return {
        "flag": rng.choice([True, False]),
        "mode": rng.choice(["a", "b", "c"]),
        "count": rng.randint(0, 100),
    }


def chain_config(n):
    lines = ["step s0 {"]
    lines.append("}")
    for j in range(1, n):
        lines.append(f"step s{j} {{")
        lines.append(f"  needs: s{j - 1};")
        lines.append("}")
    return "\n".join(lines) + "\n"


def wide_config(n):
    return "".join(f"step s{j} {{\n}}\n" for j in range(n))
