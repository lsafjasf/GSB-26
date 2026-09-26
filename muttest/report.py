"""Human-readable and JSON mutation score reports."""
from __future__ import annotations

import json

from .runner import INCOMPETENT, KILLED, STATUSES, SURVIVED, TIMEOUT, RunSummary


def render_text(summary: RunSummary, module_path: str, test_path: str) -> str:
    score, killed, denominator = summary.score()
    lines = []
    lines.append("=" * 72)
    lines.append("MUTATION TESTING REPORT")
    lines.append("=" * 72)
    lines.append(f"module under test : {module_path}")
    lines.append(f"test suite        : {test_path}")
    lines.append("")
    lines.append(f"mutants total     : {summary.total}")
    for status in STATUSES:
        lines.append(f"  {status:<12}    : {len(summary.by_status(status))}")
    lines.append(f"  equivalent (excluded): {len(summary.equivalents)}")
    lines.append("")
    lines.append(f"MUTATION SCORE    : {score:.1f}%  ({killed} / {denominator})")
    lines.append("denominator = total - equivalent - incompetent "
                 f"= {summary.total} - {len(summary.equivalents)} "
                 f"- {len(summary.by_status(INCOMPETENT))} = {denominator}")
    lines.append("numerator   = killed + timeout "
                 f"= {len(summary.by_status(KILLED))} + "
                 f"{len(summary.by_status(TIMEOUT))} = {killed}")
    lines.append("")
    lines.append(f"performance       : {summary.executions} mutant executions "
                 f"(+{summary.cache_hits} cache hits), jobs={summary.jobs}, "
                 f"timeout={summary.timeout}s, wall time {summary.wall_time:.2f}s")
    lines.append("")

    if summary.equivalents:
        lines.append("-" * 72)
        lines.append("EQUIVALENT MUTANTS (excluded from the denominator)")
        lines.append("-" * 72)
        for r in summary.equivalents:
            lines.append(f"  {r.mutant.id}")
            lines.append(f"      line {r.mutant.lineno}: {r.mutant.detail}")
            lines.append(f"      reason: {r.equivalent_reason}")
        lines.append("")

    survivors = summary.by_status(SURVIVED)
    if survivors:
        lines.append("-" * 72)
        lines.append("SURVIVING MUTANTS (weak tests -- these need stronger assertions)")
        lines.append("-" * 72)
        for r in survivors:
            lines.append(f"  {r.mutant.id}  line {r.mutant.lineno}: "
                         f"{r.mutant.detail}")
        lines.append("")

    lines.append("-" * 72)
    lines.append("ALL MUTANTS")
    lines.append("-" * 72)
    for r in summary.results:
        tag = " [equivalent]" if r.equivalent else ""
        src = "cache" if r.from_cache else f"{r.duration:.2f}s"
        lines.append(f"  {r.status:<11} {r.mutant.id:<38} "
                     f"line {r.mutant.lineno:<4} {r.mutant.detail} "
                     f"({src}){tag}")
    return "\n".join(lines)


def render_json(summary: RunSummary, module_path: str, test_path: str) -> str:
    score, killed, denominator = summary.score()
    doc = {
        "module": module_path,
        "test_suite": test_path,
        "score": round(score, 2),
        "killed": killed,
        "denominator": denominator,
        "denominator_rule": "total - equivalent - incompetent",
        "numerator_rule": "killed + timeout",
        "totals": {s: len(summary.by_status(s)) for s in STATUSES},
        "total": summary.total,
        "equivalent": len(summary.equivalents),
        "performance": {
            "mutants": summary.total,
            "executions": summary.executions,
            "cache_hits": summary.cache_hits,
            "jobs": summary.jobs,
            "timeout_seconds": summary.timeout,
            "wall_time_seconds": round(summary.wall_time, 3),
        },
        "equivalents": [
            {"id": r.mutant.id, "line": r.mutant.lineno,
             "detail": r.mutant.detail, "reason": r.equivalent_reason}
            for r in summary.equivalents
        ],
        "mutants": [
            {"id": r.mutant.id, "kind": r.mutant.kind,
             "line": r.mutant.lineno, "detail": r.mutant.detail,
             "status": r.status, "equivalent": r.equivalent,
             "from_cache": r.from_cache,
             "duration_seconds": round(r.duration, 3)}
            for r in summary.results
        ],
    }
    return json.dumps(doc, indent=2)
