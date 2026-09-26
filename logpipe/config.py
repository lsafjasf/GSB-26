"""Declarative parsing rules and output template, loaded from a JSON file.

Adding support for a new log format means adding a new config file,
not changing code.
"""

import json

ALLOWED_TYPES = ("string", "int", "float", "bool")
ALLOWED_FORMATS = ("jsonl", "csv")


class ConfigError(ValueError):
    """Invalid rules config. ``problems`` lists every issue with its location."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("\n".join(self.problems))


class FieldRule:
    __slots__ = ("name", "type", "required", "default", "greedy")

    def __init__(self, name, type_, required, default, greedy):
        self.name = name
        self.type = type_
        self.required = required
        self.default = default
        self.greedy = greedy

    def __repr__(self):
        return (
            f"FieldRule(name={self.name!r}, type={self.type!r}, "
            f"required={self.required!r}, greedy={self.greedy!r})"
        )


class Config:
    """Validated rules: how to split/convert lines and how to emit records."""

    def __init__(self, delimiter, trim, fields, out_format, out_columns):
        self.delimiter = delimiter
        self.trim = trim
        self.fields = fields            # list[FieldRule], parse order
        self.out_format = out_format    # "jsonl" | "csv"
        self.out_columns = out_columns  # list[(field_name, output_alias)]


def load_config(path):
    """Load and validate a rules config from a JSON file."""
    with open(path, "r", encoding="utf-8") as fh:
        return loads_config(fh.read(), source=path)


def loads_config(text, source="<string>"):
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            [f"{source}:{exc.lineno}:{exc.colno}: invalid JSON: {exc.msg}"]
        ) from exc
    return validate_config(data)


def _default_matches(ftype, value):
    if ftype == "string":
        return isinstance(value, str)
    if ftype == "bool":
        return isinstance(value, bool)
    if ftype == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if ftype == "float":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return False


def validate_config(data):
    """Validate a decoded config object, collecting every problem found."""
    problems = []
    if not isinstance(data, dict):
        raise ConfigError(["root: expected a JSON object"])

    version = data.get("version", 1)
    if not isinstance(version, int) or isinstance(version, bool):
        problems.append("version: must be an integer")

    parser = data.get("parser")
    if not isinstance(parser, dict):
        problems.append("parser: missing or not an object")
        parser = {}

    delimiter = parser.get("delimiter", "|")
    if not isinstance(delimiter, str) or delimiter == "":
        problems.append("parser.delimiter: must be a non-empty string")
        delimiter = "|"

    trim = parser.get("trim", True)
    if not isinstance(trim, bool):
        problems.append("parser.trim: must be a boolean")
        trim = True

    raw_fields = parser.get("fields")
    if not isinstance(raw_fields, list) or not raw_fields:
        problems.append("parser.fields: must be a non-empty array")
        raw_fields = []

    fields = []
    seen_names = {}
    for index, raw in enumerate(raw_fields):
        loc = f"parser.fields[{index}]"
        if not isinstance(raw, dict):
            problems.append(f"{loc}: must be an object")
            continue
        name = raw.get("name")
        if not isinstance(name, str) or name == "":
            problems.append(f"{loc}.name: must be a non-empty string")
            continue
        if name in seen_names:
            problems.append(
                f"{loc}.name: duplicate field name {name!r} "
                f"(first defined at parser.fields[{seen_names[name]}].name)"
            )
        else:
            seen_names[name] = index
        ftype = raw.get("type", "string")
        if ftype not in ALLOWED_TYPES:
            problems.append(
                f"{loc}.type: unknown type {ftype!r} "
                f"(allowed: {', '.join(ALLOWED_TYPES)})"
            )
            ftype = "string"
        required = raw.get("required", False)
        if not isinstance(required, bool):
            problems.append(f"{loc}.required: must be a boolean")
            required = False
        if "optional" in raw:
            optional = raw["optional"]
            if not isinstance(optional, bool):
                problems.append(f"{loc}.optional: must be a boolean")
            elif optional == required:
                problems.append(
                    f"{loc}: conflicting flags 'required' ({required}) and "
                    f"'optional' ({optional}); 'optional' must be the "
                    "negation of 'required'"
                )
        has_default = "default" in raw
        default = raw.get("default")
        if required and has_default:
            problems.append(
                f"{loc}: field is required but declares a default; "
                "a required field cannot have a default value"
            )
        if has_default and not _default_matches(ftype, default):
            problems.append(
                f"{loc}.default: value {default!r} does not match type {ftype!r}"
            )
        greedy = raw.get("greedy", False)
        if not isinstance(greedy, bool):
            problems.append(f"{loc}.greedy: must be a boolean")
            greedy = False
        if greedy:
            if index != len(raw_fields) - 1:
                problems.append(f"{loc}.greedy: only the last field may be greedy")
            if ftype != "string":
                problems.append(f"{loc}.greedy: only string fields may be greedy")
        fields.append(
            FieldRule(name, ftype, required, default if has_default else None, greedy)
        )

    output = data.get("output", {})
    if not isinstance(output, dict):
        problems.append("output: must be an object")
        output = {}
    out_format = output.get("format", "jsonl")
    if out_format not in ALLOWED_FORMATS:
        problems.append(
            f"output.format: unknown format {out_format!r} "
            f"(allowed: {', '.join(ALLOWED_FORMATS)})"
        )
        out_format = "jsonl"

    field_names = {f.name for f in fields}
    out_columns = []
    out_spec = output.get("fields")
    if out_spec is None:
        out_columns = [(f.name, f.name) for f in fields]
    elif not isinstance(out_spec, list) or not out_spec:
        problems.append("output.fields: must be a non-empty array")
    else:
        seen_alias = {}
        for index, item in enumerate(out_spec):
            loc = f"output.fields[{index}]"
            if isinstance(item, str):
                name = alias = item
            elif isinstance(item, dict):
                name = item.get("name")
                alias = item.get("as", name)
            else:
                problems.append(f"{loc}: must be a string or an object")
                continue
            if not isinstance(name, str) or not name:
                problems.append(f"{loc}.name: must be a non-empty string")
                continue
            if name not in field_names:
                problems.append(
                    f"{loc}.name: unknown field {name!r} "
                    "(not defined in parser.fields)"
                )
            if not isinstance(alias, str) or not alias:
                problems.append(f"{loc}.as: must be a non-empty string")
                continue
            if alias in seen_alias:
                problems.append(
                    f"{loc}: duplicate output column {alias!r} "
                    f"(first defined at output.fields[{seen_alias[alias]}])"
                )
            else:
                seen_alias[alias] = index
            out_columns.append((name, alias))

    if problems:
        raise ConfigError(problems)
    return Config(delimiter, trim, fields, out_format, out_columns)
