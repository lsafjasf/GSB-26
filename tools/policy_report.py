#!/usr/bin/env python3
"""Policy entropy tables, corpus generation, and per-record classification."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import id_policies
import secure_ids


def entropy_table() -> dict[str, object]:
    rows = id_policies.legacy_estimates() + [
        id_policies.estimate_policy(policy) for policy in id_policies.POLICIES.values()
    ]
    return {
        "min_entropy_bits_floor": id_policies.MIN_ENTROPY_BITS,
        "policies": rows,
    }


def build_corpus(per_policy: int) -> list[str]:
    """One reproducible-shape corpus mixing all generations.

    Legacy rows are constructed from fixed timestamps so the file layout is
    stable; generation 2 and 3 rows are fresh random values on every run.
    """
    rows: list[str] = []
    for second in (1_700_000_000, 1_700_000_001):
        for counter in range(3):
            rows.append(f"{second:010}{counter:06}")
    rows.append("17000000")  # legacy salt: truncated epoch second
    rows.extend(secure_ids.generate_session_id() for _ in range(per_policy))
    for policy in id_policies.POLICIES.values():
        rows.extend(id_policies.generate_id(policy) for _ in range(per_policy))
    rows.extend(["", "not-an-id", "170000000000000X", "s3_tooshort"])
    return rows


def classify_lines(lines: list[str]) -> list[dict[str, object]]:
    results = []
    for line in lines:
        value = line.rstrip("\n")
        record = id_policies.classify_id(value).to_dict()
        record["verified"] = id_policies.verify_classification(
            value, id_policies.classify_id(value)
        )
        results.append(record)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    table_parser = sub.add_parser("table", help="print length/entropy estimates for every policy")
    table_parser.add_argument("--output", type=Path)

    corpus_parser = sub.add_parser("corpus", help="write a mixed-generation sample file")
    corpus_parser.add_argument("--per-policy", type=int, default=4)
    corpus_parser.add_argument("--output", type=Path, required=True)

    classify_parser = sub.add_parser("classify", help="classify one identifier per line")
    classify_parser.add_argument("input", type=Path)
    classify_parser.add_argument("--output", type=Path)

    args = parser.parse_args()

    if args.command == "table":
        rendered = json.dumps(entropy_table(), ensure_ascii=False, indent=2)
        if args.output:
            args.output.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
        return 0

    if args.command == "corpus":
        rows = build_corpus(args.per_policy)
        args.output.write_text("\n".join(rows) + "\n", encoding="utf-8")
        print(f"wrote {len(rows)} rows to {args.output}")
        return 0

    if args.command == "classify":
        lines = args.input.read_text(encoding="utf-8").splitlines()
        records = classify_lines(lines)
        rendered = "\n".join(json.dumps(r, ensure_ascii=False) for r in records)
        if args.output:
            args.output.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
        matched = sum(1 for r in records if r["matched"])
        print(f"classified {matched}/{len(records)} rows", file=sys.stderr)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
