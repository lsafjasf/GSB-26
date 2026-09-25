from __future__ import annotations

import argparse
import gc
import struct
import tracemalloc

from stream_framing import (
    BufferLimitError,
    DelimiterFramer,
    LengthLimitError,
    LengthPrefixFramer,
    encode_length_prefix,
)


MAX_PAYLOAD = 64 * 1024
DEFAULT_HEADER = struct.pack(">I", MAX_PAYLOAD)


def format_bytes(value: int) -> str:
    if value % 1024 == 0:
        return f"{value // 1024} KiB"
    return f"{value} B"


def measure_length_prefix_direct() -> dict[str, object]:
    gc.collect()
    tracemalloc.start()
    try:
        framer = LengthPrefixFramer(max_payload_size=MAX_PAYLOAD)
        message = b"d" * MAX_PAYLOAD
        tracemalloc.reset_peak()
        messages = framer.feed(encode_length_prefix(message))
        _, allocated_peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    return {
        "case": "length prefix: one 64 KiB message in one feed",
        "result": f"{len(messages)} message emitted",
        "logical_peak": framer.peak_buffer_size,
        "capacity_peak": framer.buffer_capacity,
        "allocated_peak": allocated_peak,
        "behavior": "Complete frame is emitted directly; no retained payload buffer.",
    }


def measure_length_prefix_fragmented() -> dict[str, object]:
    gc.collect()
    tracemalloc.start()
    try:
        framer = LengthPrefixFramer(max_payload_size=MAX_PAYLOAD)
        framer.feed(DEFAULT_HEADER)
        tracemalloc.reset_peak()
        framer.feed(b"abc")
        _, allocated_peak = tracemalloc.get_traced_memory()
        messages = framer.feed(b"d" * (MAX_PAYLOAD - 3))
        logical_peak = framer.peak_buffer_size
        capacity = framer.buffer_capacity
    finally:
        tracemalloc.stop()

    return {
        "case": "length prefix: 64 KiB message split across feeds",
        "result": f"{len(messages)} message after final feed",
        "logical_peak": logical_peak,
        "capacity_peak": capacity,
        "allocated_peak": allocated_peak,
        "behavior": "First partial payload lazily allocates one fixed 64 KiB buffer.",
    }


def measure_length_prefix_malicious() -> dict[str, object]:
    gc.collect()
    tracemalloc.start()
    try:
        framer = LengthPrefixFramer(max_payload_size=MAX_PAYLOAD)
        malicious_header = struct.pack(">I", 10 * 1024 * 1024)
        giant_payload = b"m" * (1024 * 1024)
        tracemalloc.reset_peak()
        try:
            framer.feed(malicious_header + giant_payload)
        except LengthLimitError as error:
            result = f"rejected at frame offset {error.frame_offset}"
        _, allocated_peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    return {
        "case": "length prefix: declares 10 MiB with 1 MiB input",
        "result": result,
        "logical_peak": framer.peak_buffer_size,
        "capacity_peak": framer.buffer_capacity,
        "allocated_peak": allocated_peak,
        "behavior": "Header is rejected before payload buffering; framer is marked broken.",
    }


def measure_delimiter_fragmented() -> dict[str, object]:
    gc.collect()
    tracemalloc.start()
    try:
        framer = DelimiterFramer(delimiter=b"\n", max_message_size=MAX_PAYLOAD)
        tracemalloc.reset_peak()
        framer.feed(b"x" * MAX_PAYLOAD)
        _, allocated_peak = tracemalloc.get_traced_memory()
        messages = framer.feed(b"\n")
        logical_peak = framer.peak_buffer_size
        capacity = framer.buffer_capacity
    finally:
        tracemalloc.stop()

    return {
        "case": "delimiter: one 64 KiB message split across feeds",
        "result": f"{len(messages)} message after delimiter",
        "logical_peak": logical_peak,
        "capacity_peak": capacity,
        "allocated_peak": allocated_peak,
        "behavior": "First byte allocates one fixed 64 KiB byte buffer.",
    }


def measure_delimiter_overflow() -> dict[str, object]:
    gc.collect()
    tracemalloc.start()
    try:
        framer = DelimiterFramer(delimiter=b"\n", max_message_size=MAX_PAYLOAD)
        tracemalloc.reset_peak()
        try:
            framer.feed(b"y" * (1024 * 1024))
        except BufferLimitError as error:
            result = (
                f"rejected at frame offset {error.frame_offset}, "
                f"stream offset {error.stream_offset}"
            )
        _, allocated_peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    return {
        "case": "delimiter: 1 MiB feed with no delimiter",
        "result": result,
        "logical_peak": framer.peak_buffer_size,
        "capacity_peak": framer.buffer_capacity,
        "allocated_peak": allocated_peak,
        "behavior": "Parsing stops at the message-size boundary; no unbounded growth.",
    }


def render_markdown(rows: list[dict[str, object]]) -> str:
    lines = [
        "# Memory Peak Measurements",
        "",
        "Configuration: Python 3, 64 KiB maximum payload/message. Allocator",
        "numbers are new traced bytes after parser setup, measured by",
        "`tracemalloc`; they include fixed parser buffers, transient output",
        "copies, and caller-side test stream allocations made during that",
        "phase. Use the fixed buffer columns for parser-retained bounds.",
        "",
        "| Case | Result | Internal logical peak | Fixed buffer capacity | `tracemalloc` peak | At-limit behavior |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        lines.append(
            "| {case} | {result} | {logical} | {capacity} | {allocated} | {behavior} |".format(
                case=row["case"],
                result=row["result"],
                logical=format_bytes(int(row["logical_peak"])),
                capacity=format_bytes(int(row["capacity_peak"])),
                allocated=format_bytes(int(row["allocated_peak"])),
                behavior=row["behavior"],
            )
        )
    lines.extend(
        [
            "",
            "Guaranteed parser-retained bounds for the default configuration:",
            "",
            "- `LengthPrefixFramer`: at most 64 KiB for a fragmented payload; complete one-shot payloads are copied directly to the returned message.",
            "- `DelimiterFramer`: at most `max_message_size + len(delimiter) - 1`, i.e. 64 KiB for the default one-byte newline.",
            "- On rejection the parser enters a broken state and later `feed` calls raise instead of accepting more data.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        help="Optional path for the generated Markdown report.",
    )
    args = parser.parse_args()

    rows = [
        measure_length_prefix_direct(),
        measure_length_prefix_fragmented(),
        measure_length_prefix_malicious(),
        measure_delimiter_fragmented(),
        measure_delimiter_overflow(),
    ]
    report = render_markdown(rows)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as output:
            output.write(report)
    print(report)


if __name__ == "__main__":
    main()
