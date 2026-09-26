"""统一埋点入口：所有埋点调用必须走 emit()。

emit() 按 fields.py 中的声明校验字段，缺字段 / 类型不符 / 未知字段 /
未知事件都会抛出 InstrumentationError，并在消息中指出埋点位置（文件:行号）。
"""

import inspect
import json
import traceback
from contextlib import contextmanager

from .event import ErrorInfo, InstrumentationError, LogEvent
from .fields import SCHEMAS

_sink = None


def _default_sink(event):
    print(json.dumps(event.render(), ensure_ascii=False))


@contextmanager
def collect():
    """收集事件（测试 / 对拍用）：with collect() as events: ..."""
    global _sink
    events = []
    old, _sink = _sink, events.append
    try:
        yield events
    finally:
        _sink = old


def emit(name, error=None, **fields):
    frame = inspect.currentframe().f_back
    where = f"{frame.f_code.co_filename}:{frame.f_lineno}"

    schema = SCHEMAS.get(name)
    if schema is None:
        raise InstrumentationError(
            f"{where}: 未声明的事件类型 {name!r}，请先在 fields.py 中声明")

    declared = {f.name: f for f in schema.fields}
    unknown = sorted(set(fields) - set(declared))
    if unknown:
        raise InstrumentationError(
            f"{where}: 事件 {name!r} 存在未声明字段 {unknown}")
    missing = [f.name for f in schema.fields if f.required and f.name not in fields]
    if missing:
        raise InstrumentationError(
            f"{where}: 事件 {name!r} 缺少必填字段 {missing}")
    for fname, value in fields.items():
        expected = declared[fname].type
        if not isinstance(value, expected):
            raise InstrumentationError(
                f"{where}: 事件 {name!r} 字段 {fname!r} 类型不符，"
                f"期望 {expected}，实际 {type(value).__name__}")

    err = None
    if error is not None:
        if not isinstance(error, BaseException):
            raise InstrumentationError(f"{where}: error 参数必须是异常对象")
        err = ErrorInfo(type=type(error).__name__, message=str(error),
                        stack="".join(traceback.format_exception(error)))

    event = LogEvent(schema, fields, err)
    (_sink or _default_sink)(event)
    return event
