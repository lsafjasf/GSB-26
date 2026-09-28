"""Cross-language comparison of template signatures."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .errors import Diagnostic, TemplateSyntaxError
from .validator import ValidationResult, validate


@dataclass
class CompareReport:
    """Result of comparing the same template across languages."""

    ok: bool
    diagnostics: List[Diagnostic] = field(default_factory=list)
    results: Dict[str, ValidationResult] = field(default_factory=dict)
    reference: Optional[str] = None

    def by_kind(self, kind: str) -> List[Diagnostic]:
        return [d for d in self.diagnostics if d.kind == kind]

    def format(self) -> str:
        lines = []
        for d in self.diagnostics:
            lines.append(str(d))
        if not lines:
            return "OK: all templates are consistent"
        return "\n".join(lines)


def compare(
    templates: Dict[str, str],
    reference: Optional[str] = None,
    check_order: bool = True,
) -> CompareReport:
    """Compare language variants of one template.

    ``templates`` maps a language tag (e.g. ``"en"``) to its template text.
    The first entry (or ``reference``) is the reference language.

    Comparison is based on the path-qualified field signature: names used
    inside an ``{#each}`` body are qualified by the loop path
    (``users[].name``) and every loop contributes an item-type entry
    (``users[]``), so fields attached to different lists - or the same
    field with different types - are detected instead of comparing only
    the flattened top-level parameters.

    Reports, per language, with positions where available:

    * per-template validation errors (re-raised validation diagnostics)
    * ``MISSING_PLACEHOLDER`` - present in reference, absent here
    * ``EXTRA_PLACEHOLDER``   - absent in reference, present here
    * ``ORDER_MISMATCH``      - relative order of shared placeholders differs
    * ``TYPE_CONFLICT``       - same placeholder, different merged types
    * ``OPTIONAL_CONFLICT``   - optional in one language, required in another
    """
    diagnostics: List[Diagnostic] = []
    results: Dict[str, ValidationResult] = {}

    for lang, src in templates.items():
        try:
            res = validate(src)
        except TemplateSyntaxError as exc:
            diagnostics.append(
                Diagnostic("SYNTAX_ERROR", str(exc), pos=exc.pos, lang=lang)
            )
            continue
        results[lang] = res
        for err in res.errors:
            diagnostics.append(
                Diagnostic(err.kind, err.message, pos=err.pos, lang=lang)
            )

    if not results:
        return CompareReport(ok=False, diagnostics=diagnostics, results=results)

    def describe(key: str) -> str:
        if key.endswith("[]"):
            return f"item type of list '{key[:-2]}'"
        if "[" in key:
            return f"field '{key}'"
        return f"placeholder '{key}'"

    ref_lang = reference or next(iter(results))
    if ref_lang not in results:
        # Fall back to the first valid template as reference.
        ref_lang = next(iter(results))
    ref = results[ref_lang]
    ref_names = set(ref.fields)

    for lang, res in results.items():
        if lang == ref_lang:
            continue
        names = set(res.fields)

        for name in sorted(ref_names - names):
            diagnostics.append(
                Diagnostic(
                    "MISSING_PLACEHOLDER",
                    f"{describe(name)} exists in '{ref_lang}' "
                    f"({ref.fields[name].first_pos}) but is missing here",
                    lang=lang,
                )
            )
        for name in sorted(names - ref_names):
            diagnostics.append(
                Diagnostic(
                    "EXTRA_PLACEHOLDER",
                    f"{describe(name)} does not exist in '{ref_lang}'",
                    pos=res.fields[name].first_pos,
                    lang=lang,
                )
            )

        for name in sorted(ref_names & names):
            rp, lp = ref.fields[name], res.fields[name]
            if rp.type != lp.type:
                diagnostics.append(
                    Diagnostic(
                        "TYPE_CONFLICT",
                        f"{describe(name)} is '{rp.type}' in "
                        f"'{ref_lang}' but '{lp.type}' here",
                        pos=lp.first_pos,
                        lang=lang,
                    )
                )
            if rp.optional != lp.optional:
                diagnostics.append(
                    Diagnostic(
                        "OPTIONAL_CONFLICT",
                        f"{describe(name)} is "
                        f"{'optional' if rp.optional else 'required'} in "
                        f"'{ref_lang}' but "
                        f"{'optional' if lp.optional else 'required'} here",
                        pos=lp.first_pos,
                        lang=lang,
                    )
                )

        if check_order:
            ref_order = [k for k in ref.field_order if not k.endswith("[]")]
            res_order = [k for k in res.field_order if not k.endswith("[]")]
            common = [n for n in ref_order if n in names]
            actual = [n for n in res_order if n in ref_names]
            if common != actual:
                diagnostics.append(
                    Diagnostic(
                        "ORDER_MISMATCH",
                        f"placeholder order differs from '{ref_lang}': "
                        f"expected {common}, got {actual}",
                        lang=lang,
                    )
                )

    return CompareReport(
        ok=not diagnostics,
        diagnostics=diagnostics,
        results=results,
        reference=ref_lang,
    )
