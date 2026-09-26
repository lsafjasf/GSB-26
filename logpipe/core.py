"""Line parsing and the batch pipeline."""

import json
from dataclasses import dataclass

from .formats import make_writer


class LineError(ValueError):
    """A single input line could not be converted."""


@dataclass
class Stats:
    total: int = 0
    ok: int = 0
    failed: int = 0


def convert_value(raw, ftype):
    if ftype == "string":
        return raw
    if ftype == "int":
        return int(raw)
    if ftype == "float":
        return float(raw)
    if ftype == "bool":
        lowered = raw.strip().lower()
        if lowered in ("true", "1", "yes", "y"):
            return True
        if lowered in ("false", "0", "no", "n"):
            return False
        raise ValueError(f"not a boolean: {raw!r}")
    raise ValueError(f"unknown type: {ftype!r}")


def parse_line(text, config):
    """Convert one line into an ordered dict of typed values."""
    parts = text.split(config.delimiter)
    if config.trim:
        parts = [part.strip() for part in parts]
    fields = config.fields
    if fields[-1].greedy and len(parts) > len(fields):
        parts = parts[: len(fields) - 1] + [
            config.delimiter.join(parts[len(fields) - 1 :])
        ]
    if len(parts) != len(fields):
        raise LineError(f"expected {len(fields)} fields, got {len(parts)}")
    record = {}
    for rule, raw in zip(fields, parts):
        if raw == "":
            if rule.required:
                raise LineError(f"missing required field {rule.name!r}")
            record[rule.name] = rule.default
            continue
        try:
            record[rule.name] = convert_value(raw, rule.type)
        except ValueError:
            raise LineError(
                f"field {rule.name!r}: cannot convert {raw!r} to {rule.type}"
            ) from None
    return record


def run_pipeline(lines, config, out_stream, err_stream):
    """Convert a batch of lines.

    A failing line never stops the batch: it is reported to ``err_stream``
    as a JSON object with line number, raw content and reason, and the
    failure is counted in the returned stats.
    """
    writer = make_writer(config.out_format, out_stream, config.out_columns)
    stats = Stats()
    for line_no, raw_line in enumerate(lines, start=1):
        text = raw_line.rstrip("\n").rstrip("\r")
        if text == "":
            continue
        stats.total += 1
        try:
            record = parse_line(text, config)
        except LineError as exc:
            stats.failed += 1
            err_stream.write(
                json.dumps(
                    {"line": line_no, "raw": text, "error": str(exc)},
                    ensure_ascii=False,
                )
                + "\n"
            )
            continue
        writer.write_record(record)
        stats.ok += 1
    return stats
