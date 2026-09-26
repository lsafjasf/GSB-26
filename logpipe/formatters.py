"""输出格式化器：jsonl / csv。同一记录的字段顺序由输出模板决定。"""
from __future__ import annotations

import csv
import io
import json
from typing import Any, Iterable, TextIO

from .config import OutputTemplate


def ordered_pairs(record: dict[str, Any], template: OutputTemplate) -> list[tuple[str, Any]]:
    """按模板字段顺序取出 (字段名, 值)，缺失字段补 None。"""
    return [(name, record.get(name)) for name in template.fields]


def format_record(record: dict[str, Any], template: OutputTemplate) -> str:
    pairs = ordered_pairs(record, template)
    if template.format == "jsonl":
        return json.dumps(dict(pairs), ensure_ascii=False)
    if template.format == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="")
        writer.writerow([_csv_value(value) for _, value in pairs])
        return buf.getvalue()
    raise ValueError(f"未知输出格式: {template.format}")


def write_header(out: TextIO, template: OutputTemplate) -> None:
    if template.format == "csv" and template.csv_header:
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(template.fields)


def _csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def format_error(line_no: int, raw: str, reason: str) -> str:
    """失败行单独输出为 JSONL，包含行号、原文与原因。"""
    return json.dumps(
        {"line": line_no, "raw": raw.rstrip("\r\n"), "error": reason},
        ensure_ascii=False,
    )
