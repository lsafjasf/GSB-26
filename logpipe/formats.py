"""Output formatters. A new format = one new writer class + registry entry."""

import csv
import json


def render_value(value):
    """Canonical text form of a typed value (used by text formats and tests)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


class JsonlWriter:
    """One JSON object per line; key order follows the output template."""

    def __init__(self, stream, columns):
        self.stream = stream
        self.columns = columns

    def write_record(self, record):
        obj = {alias: record[name] for name, alias in self.columns}
        self.stream.write(json.dumps(obj, ensure_ascii=False) + "\n")


class CsvWriter:
    """Header row of output aliases, then one row per record."""

    def __init__(self, stream, columns):
        self.writer = csv.writer(stream, lineterminator="\n")
        self.columns = columns
        self.writer.writerow([alias for _, alias in columns])

    def write_record(self, record):
        self.writer.writerow([render_value(record[name]) for name, _ in self.columns])


_WRITERS = {"jsonl": JsonlWriter, "csv": CsvWriter}


def make_writer(name, stream, columns):
    try:
        writer_cls = _WRITERS[name]
    except KeyError:
        raise ValueError(f"unknown output format: {name!r}") from None
    return writer_cls(stream, columns)
