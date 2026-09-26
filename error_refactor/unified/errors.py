"""统一错误层：分类 / 错误码 / 原始原因 / 上下文 / 可重试。

新增一类错误只需在 ERROR_REGISTRY 增加一个条目（唯一改动位置）。
禁止在业务代码里拼接自由文本作为错误的唯一信息：
消息一律由注册表模板 + 结构化 context 渲染。
"""

from enum import Enum
from typing import Any, Dict, NamedTuple, Optional


class Category(Enum):
    NETWORK = "network"
    STORAGE = "storage"
    AUTH = "auth"
    VALIDATION = "validation"
    INTERNAL = "internal"


class ErrorDef(NamedTuple):
    category: Category
    retryable: bool
    template: str  # 用 context 字段做 str.format 渲染


ERROR_REGISTRY: Dict[str, ErrorDef] = {
    "NET_CONNECT_TIMEOUT": ErrorDef(Category.NETWORK, True, "connect to {host}:{port} timed out"),
    "NET_CONNECT_REFUSED": ErrorDef(Category.NETWORK, True, "connect to {host}:{port} refused"),
    "NET_HTTP_5XX": ErrorDef(Category.NETWORK, True, "HTTP {status} from {url}"),
    "NET_HTTP_4XX": ErrorDef(Category.NETWORK, False, "HTTP {status} from {url}"),
    "STORAGE_DISK_FULL": ErrorDef(Category.STORAGE, False, "no space left when writing {path}"),
    "STORAGE_IO": ErrorDef(Category.STORAGE, True, "I/O error {errno} when writing {path}"),
    "AUTH_TOKEN_EXPIRED": ErrorDef(Category.AUTH, False, "token expired"),
    "AUTH_FORBIDDEN": ErrorDef(Category.AUTH, False, "forbidden: insufficient scope"),
    "VALIDATION_EMPTY_TOKEN": ErrorDef(Category.VALIDATION, False, "token must not be empty"),
    "INTERNAL_UNEXPECTED": ErrorDef(Category.INTERNAL, False, "unexpected error: {detail}"),
}


class AppError(Exception):
    """统一错误类型。code 必须存在于 ERROR_REGISTRY，否则是编程错误。"""

    def __init__(self, code: str, *, cause: Optional[BaseException] = None,
                 retryable: Optional[bool] = None, **context: Any):
        defn = ERROR_REGISTRY[code]  # 未注册 -> KeyError，属 bug，立即暴露
        self.code = code
        self.category = defn.category
        self.retryable = defn.retryable if retryable is None else retryable
        self.context: Dict[str, Any] = dict(context)
        self.cause = cause
        super().__init__(defn.template.format(**context))

    def __repr__(self):
        return "AppError(%s/%s, retryable=%s, context=%r)" % (
            self.category.value, self.code, self.retryable, self.context)


def iter_chain(err: BaseException):
    """沿 AppError.cause / __cause__ 一路追溯到底层原因。"""
    seen = set()
    while err is not None and id(err) not in seen:
        seen.add(id(err))
        yield err
        nxt = getattr(err, "cause", None) or getattr(err, "__cause__", None)
        err = nxt


def format_chain(err: BaseException) -> str:
    """把原因链渲染成可贴进工单/日志的多行文本。"""
    lines = []
    for depth, node in enumerate(iter_chain(err)):
        if isinstance(node, AppError):
            lines.append("[%d] %s/%s retryable=%s: %s | context=%r" % (
                depth, node.category.value, node.code, node.retryable, node, node.context))
        else:
            lines.append("[%d] root %s: %s" % (depth, type(node).__name__, node))
    return "\n".join(lines)
