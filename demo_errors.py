"""Prints a sample error report and a validator-vs-renderer comparison table.

Usage: python3 demo_errors.py
"""

from tplcheck import RenderError, TemplateSyntaxError, compare, render, validate

TEMPLATES = {
    "en": (
        "Hello {name:str}, you have {count:int} new messages since {since:date}.\n"
        "{#each msgs as m}- {m:str}{/each}\n"
        "{#if vip}Thanks for being a VIP!{/if}"
    ),
    # zh: missing {since}, {count} typed str, order swapped, extra {level}
    "zh": (
        "你有 {count:str} 条新消息，{name:str}。\n"
        "{#each msgs as m}- {m:str}{/each}\n"
        "{#if vip}感谢成为 VIP！{/if}\n"
        "等级：{level:int}"
    ),
    # ja: references loop var outside its section
    "ja": "{name:str} さん、{count:int} 件。\n{#each msgs as m}- {m:str}{/each}\n最新: {m}",
}

DIFF_CASES = [
    ("{x:int} + {y:str}", {"x": 1, "y": "a"}),
    ("{x:int} {x:str}", {"x": 1}),
    ("{#each xs as x}{x}{/each}{x}", {"xs": [1]}),
    ("{n:int}{#each n as i}{i}{/each}", {"n": 3}),
    ("{f:str}{#if f}yes{/if}", {"f": "y"}),
    ("{#if a}{#each xs as x}{x:int}{/each}{/if}", {"a": True, "xs": [1, 2]}),
    ("{a?} {b}", {"b": "z"}),
]


def main():
    print("=" * 72)
    print("1) Cross-language comparison report (reference: en)")
    print("=" * 72)
    report = compare(TEMPLATES)
    print(report.format())
    print(f"\nconsistent: {report.ok}")

    print()
    print("=" * 72)
    print("2) Single-template validation")
    print("=" * 72)
    bad = "{#each items as it}{it:str}{/each}\nLast item: {it}\nTotal: {total:int} {total:str}"
    result = validate(bad)
    print(f"template: {bad!r}")
    print(f"valid: {result.ok}")
    for err in result.errors:
        print(f"  {err}")

    print()
    print("=" * 72)
    print("3) Differential check: validator verdict vs. renderer outcome")
    print("=" * 72)
    print(f"{'template':52} {'validator':10} renderer")
    print("-" * 72)
    for src, args in DIFF_CASES:
        try:
            res = validate(src)
            verdict = "ACCEPT" if res.ok else "REJECT"
            verr = ""
        except TemplateSyntaxError as exc:
            res, verdict, verr = None, "REJECT", str(exc)
        try:
            out = render(src, args)
            routcome = f"ok -> {out!r}"
        except (RenderError, TemplateSyntaxError) as exc:
            routcome = f"FAIL ({type(exc).__name__}: {exc})"
        shown = src if len(src) <= 49 else src[:46] + "..."
        print(f"{shown:52} {verdict:10} {routcome}")


if __name__ == "__main__":
    main()
