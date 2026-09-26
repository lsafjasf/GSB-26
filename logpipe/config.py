"""规则 / 输出配置的加载与校验。

所有校验错误都带 JSON 路径位置信息，例如 ``parse.fields[2].type``。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

SUPPORTED_TYPES = ("string", "int", "float", "bool")
SUPPORTED_FORMATS = ("jsonl", "csv")


class ConfigError(ValueError):
    """配置非法。message 中已包含出错位置。"""

    def __init__(self, path: str, message: str):
        self.path = path
        super().__init__(f"{path}: {message}")


@dataclass
class FieldRule:
    name: str
    type: str = "string"
    required: bool = True
    default: Any = None
    has_default: bool = False


@dataclass
class ParseRules:
    fields: list[FieldRule]
    delimiter: Optional[str] = "|"
    regex: Optional[re.Pattern] = None
    trim: bool = True


@dataclass
class OutputTemplate:
    format: str = "jsonl"
    fields: list[str] = field(default_factory=list)  # 空 = 按规则全量输出
    csv_header: bool = True


def _load_json(path: str, what: str) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise ConfigError(what, f"配置文件不存在: {path}") from None
    except json.JSONDecodeError as exc:
        raise ConfigError(what, f"JSON 语法错误 {path}:{exc.lineno}:{exc.colno}: {exc.msg}") from None


def _check_field(idx: int, raw: Any) -> FieldRule:
    path = f"parse.fields[{idx}]"
    if not isinstance(raw, dict):
        raise ConfigError(path, "字段规则必须是对象")
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise ConfigError(f"{path}.name", "字段名必须是非空字符串")
    ftype = raw.get("type", "string")
    if ftype not in SUPPORTED_TYPES:
        raise ConfigError(
            f"{path}.type",
            f"未知类型 {ftype!r}（字段 {name!r}），支持: {', '.join(SUPPORTED_TYPES)}",
        )
    required = raw.get("required", True)
    if not isinstance(required, bool):
        raise ConfigError(f"{path}.required", f"字段 {name!r} 的 required 必须是布尔值")
    has_default = "default" in raw
    if required and has_default:
        raise ConfigError(
            path,
            f"字段 {name!r} 声明为必填(required)却又提供 default，"
            "必填与可选（带默认值）语义冲突",
        )
    return FieldRule(name=name, type=ftype, required=required,
                     default=raw.get("default"), has_default=has_default)


def load_rules(path: str) -> ParseRules:
    data = _load_json(path, "rules")
    if not isinstance(data, dict):
        raise ConfigError("rules", "规则配置必须是 JSON 对象")
    parse = data.get("parse")
    if not isinstance(parse, dict):
        raise ConfigError("parse", "缺少 parse 对象")

    delimiter = parse.get("delimiter")
    regex_src = parse.get("regex")
    if delimiter is not None and regex_src is not None:
        raise ConfigError("parse", "delimiter 与 regex 只能二选一")
    if delimiter is None and regex_src is None:
        delimiter = "|"
    if delimiter is not None and (not isinstance(delimiter, str) or delimiter == ""):
        raise ConfigError("parse.delimiter", "delimiter 必须是非空字符串")
    regex = None
    if regex_src is not None:
        try:
            regex = re.compile(regex_src)
        except re.error as exc:
            raise ConfigError("parse.regex", f"正则非法: {exc}") from None

    raw_fields = parse.get("fields")
    if not isinstance(raw_fields, list) or not raw_fields:
        raise ConfigError("parse.fields", "字段列表必须是非空数组")

    fields: list[FieldRule] = []
    seen: dict[str, int] = {}
    for idx, raw in enumerate(raw_fields):
        rule = _check_field(idx, raw)
        if rule.name in seen:
            raise ConfigError(
                f"parse.fields[{idx}].name",
                f"字段重名 {rule.name!r}（首次出现于 parse.fields[{seen[rule.name]}]）",
            )
        seen[rule.name] = idx
        fields.append(rule)

    trim = parse.get("trim", True)
    if not isinstance(trim, bool):
        raise ConfigError("parse.trim", "trim 必须是布尔值")
    return ParseRules(fields=fields, delimiter=delimiter, regex=regex, trim=trim)


def load_output(path: str, rules: ParseRules) -> OutputTemplate:
    data = _load_json(path, "output")
    if not isinstance(data, dict):
        raise ConfigError("output", "输出配置必须是 JSON 对象")
    fmt = data.get("format", "jsonl")
    if fmt not in SUPPORTED_FORMATS:
        raise ConfigError("output.format",
                          f"未知输出格式 {fmt!r}，支持: {', '.join(SUPPORTED_FORMATS)}")
    known = {f.name for f in rules.fields}
    names = data.get("fields", [f.name for f in rules.fields])
    if not isinstance(names, list) or not names:
        raise ConfigError("output.fields", "输出字段模板必须是非空数组")
    seen: set[str] = set()
    for idx, name in enumerate(names):
        if name not in known:
            raise ConfigError(f"output.fields[{idx}]",
                              f"模板引用了规则中不存在的字段 {name!r}")
        if name in seen:
            raise ConfigError(f"output.fields[{idx}]", f"输出模板中字段重复 {name!r}")
        seen.add(name)
    csv_header = data.get("csv_header", True)
    if not isinstance(csv_header, bool):
        raise ConfigError("output.csv_header", "csv_header 必须是布尔值")
    return OutputTemplate(format=fmt, fields=list(names), csv_header=csv_header)
