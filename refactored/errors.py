"""统一错误类型：分类、错误码、原始原因、上下文、是否可重试。

新增一类错误只需在 _REGISTRY 中增加一个条目（唯一定义位置），
各模块通过 AppError("CODE", cause=..., **context) 使用，
禁止以拼接自由文本作为错误的唯一信息载体。
"""

import enum
from dataclasses import dataclass
from typing import Any, Dict, Iterator, Optional


class Category(enum.Enum):
    NETWORK = "network"
    STORAGE = "storage"
    CACHE = "cache"
    VALIDATION = "validation"
    INTERNAL = "internal"


@dataclass(frozen=True)
class ErrorSpec:
    category: Category
    retryable: bool
    message_template: str  # 用 {context_key} 引用上下文字段


# === 新增错误类型的唯一改动位置 ===
_REGISTRY: Dict[str, ErrorSpec] = {
    "NET_TIMEOUT": ErrorSpec(
        Category.NETWORK, True, "request to {url} timed out after {timeout}s"),
    "NET_CONN_REFUSED": ErrorSpec(
        Category.NETWORK, True, "connection to {url} refused"),
    "STORE_CONN": ErrorSpec(
        Category.STORAGE, True, "cannot connect to store {dsn}"),
    "STORE_QUERY": ErrorSpec(
        Category.STORAGE, False, "store query failed: {sql}"),
    "CACHE_BACKEND_DOWN": ErrorSpec(
        Category.CACHE, True, "cache backend unavailable for key {key}"),
    "PROFILE_LOAD_FAILED": ErrorSpec(
        Category.INTERNAL, False, "failed to load profile for user {user_id}"),
}


class AppError(Exception):
    """统一错误类型。

    code      错误码，必须在 _REGISTRY 中注册
    category  由注册表推导，调用方不可伪造
    context   结构化上下文（url、dsn、user_id 等）
    cause     原始原因，经 __cause__ 串成原因链
    retryable 默认可由注册表推导，允许单点覆盖
    """

    def __init__(self, code: str, *, cause: Optional[BaseException] = None,
                 retryable: Optional[bool] = None, **context: Any) -> None:
        try:
            spec = _REGISTRY[code]
        except KeyError:
            raise ValueError(
                "unregistered error code %r; add it to "
                "refactored/errors.py:_REGISTRY" % (code,)) from None
        self.code = code
        self.category = spec.category
        self.context = dict(context)
        self.retryable = spec.retryable if retryable is None else retryable
        super().__init__(spec.message_template.format(**self.context))
        if cause is not None:
            self.__cause__ = cause

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "category": self.category.value,
            "retryable": self.retryable,
            "context": dict(self.context),
            "message": str(self),
        }


def iter_chain(exc: BaseException) -> Iterator[BaseException]:
    """从最外层到最底层原因依次产出。"""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__


def format_chain(exc: BaseException) -> str:
    """原因链追溯输出，底层原因在最后。"""
    lines = []
    for depth, err in enumerate(iter_chain(exc)):
        indent = "  " * depth
        prefix = "caused by: " if depth else ""
        if isinstance(err, AppError):
            ctx = " ".join("%s=%r" % kv for kv in sorted(err.context.items()))
            lines.append("%s%s%s [%s] retryable=%s: %s (%s)" % (
                indent, prefix, err.code, err.category.value,
                err.retryable, err, ctx))
        else:
            lines.append("%s%s%s: %s" % (
                indent, prefix, type(err).__name__, err))
    return "\n".join(lines)
