#!/usr/bin/env python3
"""Collect collision and distribution statistics for secure identifiers."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import secure_ids


def common_prefix_length(left: str, right: str) -> int:
    size = 0
    for a, b in zip(left, right):
        if a != b:
            break
        size += 1
    return size


def largest_prefix_bucket(values: list[str], prefix_length: int) -> dict[str, int | str]:
    largest_prefix = ""
    largest_size = 0
    current_prefix = ""
    current_size = 0
    for value in values:
        prefix = value[:prefix_length]
        if prefix == current_prefix:
            current_size += 1
        else:
            if current_size > largest_size:
                largest_size = current_size
                largest_prefix = current_prefix
            current_prefix = prefix
            current_size = 1
    if current_size > largest_size:
        largest_size = current_size
        largest_prefix = current_prefix
    return {"prefix_length": prefix_length, "largest_bucket_size": largest_size, "prefix": largest_prefix}


def collect(count: int, length: int) -> dict[str, object]:
    started = time.perf_counter()
    values = [secure_ids.generate_session_id(length) for _ in range(count)]
    generation_seconds = time.perf_counter() - started

    duplicate_count = count - len(set(values))
    symbol_counts = Counter("".join(values))
    expected_symbol_count = count * length / len(secure_ids.ALPHABET)
    chi_square = sum(
        (symbol_counts[symbol] - expected_symbol_count) ** 2 / expected_symbol_count
        for symbol in secure_ids.ALPHABET
    )

    bit_table = {symbol: f"{index:06b}" for index, symbol in enumerate(secure_ids.ALPHABET)}
    total_bits = count * length * 6
    one_bits = sum(bit_table[symbol].count("1") for value in values for symbol in value)

    sorted_started = time.perf_counter()
    sorted_values = sorted(values)
    sort_seconds = time.perf_counter() - sorted_started

    prefix_histogram: Counter[int] = Counter()
    longest_pair = ("", "", 0)
    for index in range(1, count):
        left = sorted_values[index - 1]
        right = sorted_values[index]
        prefix_length = common_prefix_length(left, right)
        prefix_histogram[prefix_length] += 1
        if prefix_length > longest_pair[2]:
            longest_pair = (left, right, prefix_length)

    return {
        "count": count,
        "length": length,
        "duplicate_count": duplicate_count,
        "unique_count": count - duplicate_count,
        "entropy_bits": length * 6,
        "generation_seconds": round(generation_seconds, 6),
        "sort_seconds": round(sort_seconds, 6),
        "symbol_distribution": {
            "alphabet_size": len(secure_ids.ALPHABET),
            "expected_per_symbol": round(expected_symbol_count, 3),
            "observed_min": min(symbol_counts[symbol] for symbol in secure_ids.ALPHABET),
            "observed_max": max(symbol_counts[symbol] for symbol in secure_ids.ALPHABET),
            "chi_square": round(chi_square, 3),
        },
        "bit_distribution": {
            "bits_per_symbol": 6,
            "total_bits": total_bits,
            "one_bits": one_bits,
            "zero_bits": total_bits - one_bits,
            "one_bit_fraction": round(one_bits / total_bits, 8),
            "zero_bit_fraction": round((total_bits - one_bits) / total_bits, 8),
        },
        "lexicographically_adjacent_prefix_histogram": dict(sorted(prefix_histogram.items())),
        "longest_common_prefix": {
            "length": longest_pair[2],
            "left": longest_pair[0],
            "right": longest_pair[1],
        },
        "largest_prefix_buckets": [
            largest_prefix_bucket(sorted_values, prefix_length)
            for prefix_length in (4, 8, 12, 16)
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=1_000_000)
    parser.add_argument("--length", type=int, default=secure_ids.DEFAULT_SESSION_ID_LENGTH)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    result = collect(args.count, args.length)
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
