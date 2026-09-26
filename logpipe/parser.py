"""按声明式规则把一行文本解析为有序字段字典。"""
from __future__ import annotations

from typing import Any, Optional

from .config import ParseRules


class ParseError(ValueError):
    """单行解析失败。message 为人类可读原因。"""


def _convert(rule_name: str, ftype: str, text: str) -> Any:
    if ftype == "string":
        return text
    if ftype == "int":
        try:
            return int(text, 10)
        except ValueError:
            raise ParseError(f"字段 {rule_name!r} 期望 int，实际 {text!r}") from None
    if ftype == "float":
        try:
            return float(text)
        except ValueError:
            raise ParseError(f"字段 {rule_name!r} 期望 float，实际 {text!r}") from None
    if ftype == "bool":
        low = text.lower()
        if low in ("true", "1", "yes"):
            return True
        if low in ("false", "0", "no"):
            return False
        raise ParseError(f"字段 {rule_name!r} 期望 bool(true/false/1/0/yes/no)，实际 {text!r}")
    raise ParseError(f"字段 {rule_name!r} 配置了未知类型 {ftype!r}")  # 理论上加载时已拦截


def parse_line(line: str, rules: ParseRules) -> dict[str, Any]:
    """解析单行，返回保持规则字段顺序的 dict。失败抛 ParseError。"""
    text = line.rstrip("\r\n")
    if rules.regex is not None:
        match = rules.regex.match(text)
        if match is None:
            raise ParseError("整行不匹配 parse.regex")
        parts = list(match.groups())
    else:
        parts = text.split(rules.delimiter)
    if rules.trim:
        parts = [p.strip() for p in parts]

    fields = rules.fields
    if len(parts) > len(fields):
        raise ParseError(f"列数过多：期望 {len(fields)}，实际 {len(parts)}")

    record: dict[str, Any] = {}
    for idx, rule in enumerate(fields):
        value: Optional[str] = parts[idx] if idx < len(parts) else None
        if value is None or value == "":
            if rule.required:
                if value is None:
                    raise ParseError(
                        f"缺少必填字段 {rule.name!r}（期望 {len(fields)} 列，实际 {len(parts)} 列）")
                raise ParseError(f"必填字段 {rule.name!r} 为空")
            record[rule.name] = rule.default if rule.has_default else None
            continue
        record[rule.name] = _convert(rule.name, rule.type, value)
    return record
