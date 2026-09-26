"""Overhead table and throughput benchmark for SEC-DED blocks."""

from __future__ import annotations

import argparse
import random
import statistics
import sys
import time
from pathlib import Path

from secded import SECDED, Status


def sec_parity_bits(data_bits: int) -> int:
    p = 0
    while (1 << p) < data_bits + p + 1:
        p += 1
    return p


def median_rate(code: SECDED, received_words, target_seconds: float, repeats: int):
    rounds = 1
    elapsed = 0.0
    while elapsed < target_seconds:
        started = time.perf_counter()
        for _ in range(rounds):
            for word in received_words:
                code.decode(word)
        elapsed = time.perf_counter() - started
        if elapsed < target_seconds:
            next_rounds = int(target_seconds * rounds / max(elapsed, 1e-9))
            rounds = min(max(next_rounds, rounds * 2), 2048)

    samples = []
    total_blocks = rounds * len(received_words)
    for _ in range(repeats):
        started = time.perf_counter()
        for _ in range(rounds):
            for word in received_words:
                code.decode(word)
        samples.append(total_blocks / (time.perf_counter() - started))
    return statistics.median(samples)


def build_words(code: SECDED, block_count: int, seed: int):
    rng = random.Random(seed)
    valid = [code.encode(rng.getrandbits(code.data_bits)) for _ in range(block_count)]

    single = []
    rejected = []
    for index, word in enumerate(valid):
        single_position = index % code.total_length
        first = (index * 2 + 1) % code.total_length
        second = (first + 1 + (index % (code.total_length - 1))) % code.total_length
        if second == first:
            second = (second + 1) % code.total_length
        single.append(word ^ (1 << single_position))
        rejected.append(word ^ (1 << first) ^ (1 << second))
    return valid, single, rejected


def verify_scenarios(code: SECDED, clean, single, rejected) -> None:
    assert all(code.decode(word).status is Status.NO_ERROR for word in clean)
    assert all(
        code.decode(word).status is Status.SINGLE_ERROR_CORRECTED
        for word in single
    )
    assert all(
        code.decode(word).status is Status.UNCORRECTABLE_ERROR_DETECTED
        for word in rejected
    )


def format_rate(blocks_per_second: float) -> str:
    return f"{blocks_per_second:,.0f}"


def benchmark(widths, block_count: int, target_seconds: float, repeats: int) -> str:
    lines = [
        "# SEC-DED overhead and throughput",
        "",
        "Python: " + ".".join(map(str, sys.version_info[:3])),
        "Timing method: median wall-clock rate over repeated fixed batches.",
        "",
        "## Overhead",
        "",
        "| k data bits | p Hamming bits | r=p+1 total | n code bits | SEC bound p | payload % | syndrome slack |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]

    for k in widths:
        p_sec = sec_parity_bits(k)
        code = SECDED(k)
        slack = (1 << code.hamming_parity_bits) - code.total_length
        lines.append(
            f"| {k} | {code.hamming_parity_bits} | {code.total_parity_bits} | "
            f"{code.total_length} | {p_sec} | {100 * k / code.total_length:.2f}% | {slack} |"
        )

    lines.extend(
        [
            "",
            "## Decode throughput",
            "",
            f"Random blocks per width: {block_count}. Rates are blocks/s; "
            "payload Mibit/s is shown for clean blocks.",
            "",
            "| k | clean blocks/s | clean Mibit/s | single corrected/s | rejected double-error/s |",
            "|---:|---:|---:|---:|---:|",
        ]
    )

    for k in widths:
        code = SECDED(k)
        clean, single, rejected = build_words(code, block_count, seed=9400 + k)
        verify_scenarios(code, clean, single, rejected)
        clean_rate = median_rate(code, clean, target_seconds, repeats)
        single_rate = median_rate(code, single, target_seconds, repeats)
        rejected_rate = median_rate(code, rejected, target_seconds, repeats)
        payload_mibit = clean_rate * k / 1_048_576
        lines.append(
            f"| {k} | {format_rate(clean_rate)} | {payload_mibit:.2f} | "
            f"{format_rate(single_rate)} | {format_rate(rejected_rate)} |"
        )

    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--blocks", type=int, default=1024)
    parser.add_argument("--target-seconds", type=float, default=0.25)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--output", type=Path, default=Path("benchmark_results.md"))
    parser.add_argument(
        "--widths",
        type=int,
        nargs="+",
        default=(4, 8, 11, 16, 32, 64, 128, 256, 512, 1024, 2048),
    )
    args = parser.parse_args()

    report = benchmark(
        args.widths,
        args.blocks,
        args.target_seconds,
        args.repeats,
    )
    args.output.write_text(report, encoding="utf-8")
    print(report)
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
