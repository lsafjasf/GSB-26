"""csvcheck - CSV data validation library (Python 3 standard library only).

Features
--------
- Collects ALL errors in one pass; each error carries file, line, column,
  field name, expected value and actual value.
- Configurable error limit (`max_errors`) and severity levels. When the
  limit is hit, collection stops but the result is explicitly marked as
  truncated (never pretends to be complete). Counters keep running so the
  summary still reflects the true total.
- Malformed rows (unclosed quotes, field-count mismatch, encoding errors)
  are classified separately from field-validation errors and keep a raw
  snippet of the original line.
- Machine-readable JSON summary (counts grouped by error type and field)
  plus a human-readable text report; both are generated from the same
  counters so their contents are always consistent.

CLI
---
    python3 csvcheck.py data.csv --schema schema.json \
        [--max-errors 1000] [--min-severity info|warning|error|critical] \
        [--format text|json] [--encoding utf-8] [--no-header]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field as _dc_field
from datetime import datetime

# ---------------------------------------------------------------------------
# Categories & severities
# ---------------------------------------------------------------------------

CATEGORY_MALFORMED = "malformed_row"        # row could not be parsed at all
CATEGORY_FIELD = "field_validation"         # row parsed, but a field is invalid

SEVERITY_ORDER = {"info": 0, "warning": 1, "error": 2, "critical": 3}
DEFAULT_MAX_ERRORS = 1000
SNIPPET_LENGTH = 120

_INT_RE = re.compile(r"^[+-]?\d+$")
_BOOL_VALUES = {"true", "false", "0", "1", "yes", "no"}
_DATE_FORMAT = "%Y-%m-%d"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

@dataclass
class Field:
    """Definition of one CSV column."""
    name: str
    type: str = "str"                 # str | int | float | bool | date | enum
    required: bool = True
    severity: str = "error"           # severity attached to violations of this field
    min_value: float | None = None
    max_value: float | None = None
    min_length: int | None = None
    max_length: int | None = None
    pattern: str | None = None
    choices: list | None = None       # for type == "enum"

    def __post_init__(self):
        if self.severity not in SEVERITY_ORDER:
            raise ValueError(f"unknown severity: {self.severity!r}")
        self._regex = re.compile(self.pattern) if self.pattern else None

    @classmethod
    def from_dict(cls, data: dict) -> "Field":
        known = {f for f in cls.__dataclass_fields__ if not f.startswith("_")}
        return cls(**{k: v for k, v in data.items() if k in known})


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

@dataclass
class ValidationError:
    category: str            # CATEGORY_MALFORMED | CATEGORY_FIELD
    code: str                # e.g. unclosed_quote, type_error, out_of_range ...
    file: str
    line: int                # 1-based line number in the file
    column: int | None       # 1-based field position; None for row-level errors
    field: str | None        # field name; None for malformed rows
    expected: str
    actual: str
    severity: str = "error"
    raw_snippet: str | None = None   # original line fragment (malformed rows)

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "code": self.code,
            "file": self.file,
            "line": self.line,
            "column": self.column,
            "field": self.field,
            "expected": self.expected,
            "actual": self.actual,
            "severity": self.severity,
            "raw_snippet": self.raw_snippet,
        }


# ---------------------------------------------------------------------------
# CSV line parsing (quote aware, detects unclosed quotes)
# ---------------------------------------------------------------------------

def parse_csv_line(line: str, delimiter: str = ","):
    """Parse one CSV line.

    Returns (fields, None) on success, or (None, (code, column)) where
    `column` is the 1-based char offset of the offending quote.
    """
    fields = []
    buf = []
    in_quotes = False
    quote_start = -1
    field_started = False
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if in_quotes:
            if ch == '"':
                if i + 1 < n and line[i + 1] == '"':
                    buf.append('"')
                    i += 2
                    continue
                in_quotes = False
                i += 1
                continue
            buf.append(ch)
            i += 1
            continue
        if ch == '"' and not field_started:
            in_quotes = True
            quote_start = i
            field_started = True
            i += 1
            continue
        if ch == delimiter:
            fields.append("".join(buf))
            buf = []
            field_started = False
            i += 1
            continue
        buf.append(ch)
        field_started = True
        i += 1
    if in_quotes:
        return None, ("unclosed_quote", quote_start + 1)
    fields.append("".join(buf))
    return fields, None


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class ValidationResult:
    file: str
    rows_processed: int = 0
    total_errors: int = 0          # true total, keeps counting after truncation
    truncated: bool = False        # True once max_errors was reached
    errors: list = _dc_field(default_factory=list)
    by_category: dict = _dc_field(default_factory=dict)
    by_code: dict = _dc_field(default_factory=dict)
    by_field: dict = _dc_field(default_factory=dict)
    by_severity: dict = _dc_field(default_factory=dict)

    def summary(self) -> dict:
        """Machine-readable summary (grouped counts)."""
        return {
            "file": self.file,
            "rows_processed": self.rows_processed,
            "total_errors": self.total_errors,
            "errors_collected": len(self.errors),
            "truncated": self.truncated,
            "by_category": dict(sorted(self.by_category.items())),
            "by_code": dict(sorted(self.by_code.items())),
            "by_field": dict(sorted(self.by_field.items())),
            "by_severity": dict(sorted(self.by_severity.items())),
        }

    def to_json(self, indent: int = 2) -> str:
        payload = self.summary()
        payload["errors"] = [e.to_dict() for e in self.errors]
        return json.dumps(payload, ensure_ascii=False, indent=indent)

    def to_text(self) -> str:
        """Human-readable report, generated from the same data as to_json()."""
        s = self.summary()
        lines = []
        lines.append("=" * 60)
        lines.append("CSV Validation Report")
        lines.append("=" * 60)
        lines.append(f"File: {s['file']}")
        lines.append(f"Rows processed: {s['rows_processed']}")
        lines.append(f"Total errors: {s['total_errors']}")
        lines.append(f"Errors collected: {s['errors_collected']}")
        if s["truncated"]:
            lines.append("*** TRUNCATED / 结果被截断: error limit reached, "
                         "this is NOT the complete error list. ***")
        else:
            lines.append("Truncated: no (complete result)")
        lines.append("")
        lines.append("Summary by category:")
        for key, count in s["by_category"].items():
            lines.append(f"  {key}: {count}")
        lines.append("Summary by code:")
        for key, count in s["by_code"].items():
            lines.append(f"  {key}: {count}")
        lines.append("Summary by field:")
        for key, count in s["by_field"].items():
            lines.append(f"  {key}: {count}")
        lines.append("Summary by severity:")
        for key, count in s["by_severity"].items():
            lines.append(f"  {key}: {count}")
        lines.append("")
        lines.append(f"Details ({s['errors_collected']} errors):")
        for e in self.errors:
            loc = f"{e.file}:{e.line}"
            if e.column is not None:
                loc += f":{e.column}"
            fld = f" [{e.field}]" if e.field else ""
            lines.append(
                f"  {loc}{fld} {e.category}/{e.code} ({e.severity}): "
                f"expected {e.expected}, actual {e.actual!r}"
            )
            if e.raw_snippet is not None:
                lines.append(f"    raw: {e.raw_snippet}")
        return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

class Validator:
    def __init__(self, schema, max_errors: int = DEFAULT_MAX_ERRORS,
                 min_severity: str = "info", encoding: str = "utf-8",
                 has_header: bool = True, delimiter: str = ","):
        if min_severity not in SEVERITY_ORDER:
            raise ValueError(f"unknown severity: {min_severity!r}")
        self.schema = list(schema)
        self.max_errors = max_errors
        self.min_severity = min_severity
        self.encoding = encoding
        self.has_header = has_header
        self.delimiter = delimiter

    # -- internal helpers ---------------------------------------------------

    def _accepts(self, severity: str) -> bool:
        return SEVERITY_ORDER[severity] >= SEVERITY_ORDER[self.min_severity]

    def _record(self, result: ValidationResult, error: ValidationError) -> None:
        """Count every error; store details only until max_errors is hit."""
        if not self._accepts(error.severity):
            return
        result.total_errors += 1
        result.by_category[error.category] = result.by_category.get(error.category, 0) + 1
        result.by_code[error.code] = result.by_code.get(error.code, 0) + 1
        field_key = error.field if error.field is not None else "(row)"
        result.by_field[field_key] = result.by_field.get(field_key, 0) + 1
        result.by_severity[error.severity] = result.by_severity.get(error.severity, 0) + 1
        if len(result.errors) < self.max_errors:
            result.errors.append(error)
        else:
            result.truncated = True

    @staticmethod
    def _snippet(text: str) -> str:
        text = text.rstrip("\r\n")
        if len(text) > SNIPPET_LENGTH:
            return text[:SNIPPET_LENGTH] + "..."
        return text

    # -- field validation ----------------------------------------------------

    def _validate_field(self, fdef: Field, value: str):
        """Return list of (code, expected, actual) for one field value."""
        problems = []
        if value == "":
            if fdef.required:
                problems.append(("required_missing", "non-empty value", value))
            return problems

        if fdef.type == "int":
            if not _INT_RE.match(value):
                problems.append(("type_error", "int", value))
                return problems
            number = int(value)
        elif fdef.type == "float":
            try:
                number = float(value)
            except ValueError:
                problems.append(("type_error", "float", value))
                return problems
        elif fdef.type == "bool":
            if value.lower() not in _BOOL_VALUES:
                problems.append(("type_error", "bool (true/false/0/1/yes/no)", value))
            return problems
        elif fdef.type == "date":
            try:
                datetime.strptime(value, _DATE_FORMAT)
            except ValueError:
                problems.append(("type_error", f"date ({_DATE_FORMAT})", value))
            return problems
        elif fdef.type == "enum":
            if fdef.choices is not None and value not in fdef.choices:
                problems.append(("not_in_choices",
                                 "one of " + ",".join(map(str, fdef.choices)), value))
            return problems
        else:  # str
            number = None

        if number is not None:
            if fdef.min_value is not None and number < fdef.min_value:
                problems.append(("out_of_range", f">= {fdef.min_value}", value))
            if fdef.max_value is not None and number > fdef.max_value:
                problems.append(("out_of_range", f"<= {fdef.max_value}", value))
        if fdef.min_length is not None and len(value) < fdef.min_length:
            problems.append(("too_short", f"length >= {fdef.min_length}", value))
        if fdef.max_length is not None and len(value) > fdef.max_length:
            problems.append(("too_long", f"length <= {fdef.max_length}", value))
        if fdef._regex is not None and not fdef._regex.search(value):
            problems.append(("pattern_mismatch", f"match /{fdef.pattern}/", value))
        return problems

    # -- main entry points ---------------------------------------------------

    def validate_file(self, path: str) -> ValidationResult:
        result = ValidationResult(file=path)
        expected_cols = len(self.schema)
        with open(path, "rb") as fh:
            for line_no, raw in enumerate(fh, start=1):
                # --- decoding (encoding errors are malformed rows) ---------
                try:
                    text = raw.decode(self.encoding)
                except UnicodeDecodeError as exc:
                    self._record(result, ValidationError(
                        category=CATEGORY_MALFORMED,
                        code="encoding_error",
                        file=path, line=line_no, column=None, field=None,
                        expected=f"valid {self.encoding}",
                        actual=repr(raw[:60]),
                        severity="critical",
                        raw_snippet=self._snippet(repr(raw)),
                    ))
                    continue
                text = text.rstrip("\r\n")

                # --- header -------------------------------------------------
                if line_no == 1 and self.has_header:
                    header, perr = parse_csv_line(text, self.delimiter)
                    if perr is None and header != [f.name for f in self.schema]:
                        self._record(result, ValidationError(
                            category=CATEGORY_MALFORMED,
                            code="header_mismatch",
                            file=path, line=line_no, column=None, field=None,
                            expected=",".join(f.name for f in self.schema),
                            actual=",".join(header),
                            severity="warning",
                            raw_snippet=self._snippet(text),
                        ))
                    continue

                result.rows_processed += 1
                self._validate_row(result, path, line_no, text, expected_cols)
        return result

    def _validate_row(self, result, path, line_no, text, expected_cols):
        fields, perr = parse_csv_line(text, self.delimiter)
        if perr is not None:
            code, col = perr
            self._record(result, ValidationError(
                category=CATEGORY_MALFORMED, code=code,
                file=path, line=line_no, column=col, field=None,
                expected="closed quote before end of line",
                actual=text[col - 1:col + 20],
                severity="error",
                raw_snippet=self._snippet(text),
            ))
            return
        if len(fields) != expected_cols:
            self._record(result, ValidationError(
                category=CATEGORY_MALFORMED,
                code="field_count_mismatch",
                file=path, line=line_no, column=None, field=None,
                expected=f"{expected_cols} fields",
                actual=f"{len(fields)} fields",
                severity="error",
                raw_snippet=self._snippet(text),
            ))
            return
        for idx, (fdef, value) in enumerate(zip(self.schema, fields), start=1):
            for code, expected, actual in self._validate_field(fdef, value):
                self._record(result, ValidationError(
                    category=CATEGORY_FIELD, code=code,
                    file=path, line=line_no, column=idx, field=fdef.name,
                    expected=expected, actual=actual,
                    severity=fdef.severity,
                ))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def load_schema(path: str):
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return [Field.from_dict(item) for item in data]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Validate a CSV file against a schema.")
    parser.add_argument("csv_file")
    parser.add_argument("--schema", required=True, help="JSON schema file")
    parser.add_argument("--max-errors", type=int, default=DEFAULT_MAX_ERRORS)
    parser.add_argument("--min-severity", default="info",
                        choices=list(SEVERITY_ORDER))
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--encoding", default="utf-8")
    parser.add_argument("--no-header", action="store_true")
    args = parser.parse_args(argv)

    validator = Validator(
        load_schema(args.schema),
        max_errors=args.max_errors,
        min_severity=args.min_severity,
        encoding=args.encoding,
        has_header=not args.no_header,
    )
    result = validator.validate_file(args.csv_file)
    sys.stdout.write(result.to_json() + "\n" if args.format == "json"
                     else result.to_text())
    return 1 if result.total_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
