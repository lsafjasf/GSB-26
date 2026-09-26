"""兼容层：既有错误类型与统一错误码之间的映射关系（单一事实来源）。

- map_legacy(exc)：对拍测试与迁移期日志用，把 legacy 异常翻译成 (code, context)。
- to_legacy(app_error)：迁移期适配器，让尚未改造的调用方继续捕获旧类型。
"""

from legacy import errors as legacy_errors
from .errors import AppError


def map_legacy(exc):
    """legacy 异常 -> (code, 关键上下文)。识别既有错误类型的映射关系。"""
    if isinstance(exc, legacy_errors.HttpError):
        code = "NET_HTTP_5XX" if exc.status >= 500 else "NET_HTTP_4XX"
        return code, {"status": exc.status, "url": exc.url}
    if isinstance(exc, legacy_errors.ConnectFailure):
        text = str(exc)
        hostport = text.split("connect to ", 1)[1].split(" ")[0]
        host, _, port = hostport.partition(":")
        code = "NET_CONNECT_TIMEOUT" if "timed out" in text else "NET_CONNECT_REFUSED"
        return code, {"host": host, "port": int(port)}
    if isinstance(exc, legacy_errors.DiskFullError):
        path = str(exc).rsplit("writing ", 1)[1]
        return "STORAGE_DISK_FULL", {"path": path}
    if isinstance(exc, legacy_errors.AuthException):
        return ("AUTH_TOKEN_EXPIRED", {}) if "expired" in str(exc) else ("AUTH_FORBIDDEN", {})
    if isinstance(exc, legacy_errors.BadInput):
        return "VALIDATION_EMPTY_TOKEN", {}
    if isinstance(exc, OSError):
        return "STORAGE_IO", {"errno": exc.errno}
    raise TypeError("unmapped legacy error: %r" % (exc,))


def to_legacy(err):
    """AppError -> 旧异常类型，供未改造的调用方在迁移期继续 except 旧类型。"""
    if not isinstance(err, AppError):
        raise TypeError("expected AppError, got %r" % (err,))
    ctx = err.context
    if err.code in ("NET_HTTP_5XX", "NET_HTTP_4XX"):
        return legacy_errors.HttpError(ctx["status"], ctx["url"])
    if err.code in ("NET_CONNECT_TIMEOUT", "NET_CONNECT_REFUSED"):
        return legacy_errors.ConnectFailure(str(err))
    if err.code == "STORAGE_DISK_FULL":
        return legacy_errors.DiskFullError(str(err))
    if err.code in ("AUTH_TOKEN_EXPIRED", "AUTH_FORBIDDEN"):
        return legacy_errors.AuthException(str(err))
    if err.code == "VALIDATION_EMPTY_TOKEN":
        return legacy_errors.BadInput(str(err))
    if err.code == "STORAGE_IO":
        import os
        return OSError(ctx.get("errno"), os.strerror(ctx.get("errno") or 0))
    return Exception(str(err))
