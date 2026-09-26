"""结构化事件类型与声明式字段定义。"""

import time
from dataclasses import dataclass

from . import masking


class InstrumentationError(TypeError):
    """埋点调用不符合字段声明时抛出（缺字段 / 类型不符 / 未知字段 / 未知事件）。"""


@dataclass(frozen=True)
class Field:
    """声明式字段：名称、类型、是否必填、可选脱敏规则。"""

    name: str
    type: tuple
    required: bool = True
    mask: str = None

    def __post_init__(self):
        object.__setattr__(self, "type",
                           self.type if isinstance(self.type, tuple) else (self.type,))
        if self.mask is not None and self.mask not in masking.RULES:
            raise ValueError(
                f"字段 {self.name!r} 引用了未知脱敏规则 {self.mask!r}，"
                f"可用规则见 masking.RULES: {sorted(masking.RULES)}")


@dataclass(frozen=True)
class EventSchema:
    """事件类型声明：事件名、分级、字段列表。"""

    name: str
    level: str
    fields: tuple


@dataclass
class ErrorInfo:
    type: str
    message: str
    stack: str


class LogEvent:
    """统一结构化事件：事件名、时间、分级、上下文字段、可选错误信息。

    self.fields 保存原始值（信息不丢失）；脱敏只发生在 render() 输出时。
    """

    def __init__(self, schema, values, error=None):
        self._schema = schema
        self.name = schema.name
        self.level = schema.level
        self.timestamp = time.time()
        self.fields = dict(values)
        self.error = error

    def render(self):
        fields = {}
        for f in self._schema.fields:
            if f.name not in self.fields:
                continue
            value = self.fields[f.name]
            fields[f.name] = masking.apply(f.mask, value) if f.mask else value
        out = {
            "event": self.name,
            "ts": self.timestamp,
            "level": self.level,
            "fields": fields,
        }
        if self.error is not None:
            out["error"] = {
                "type": self.error.type,
                "message": self.error.message,
                "stack": self.error.stack,
            }
        return out
