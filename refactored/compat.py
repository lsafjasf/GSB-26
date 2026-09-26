"""调用方兼容层：既有遗留错误类型 -> 统一 AppError 的映射。

这是识别并保留既有错误语义的唯一适配点。遗留异常只携带自由文本，
映射时按各模块既有的消息格式解析出关键上下文，保证信息不丢失。
"""

import re

from legacy import cache as legacy_cache
from legacy import net as legacy_net
from legacy import service as legacy_service
from legacy import store as legacy_store

from .errors import AppError

_NET_TIMEOUT_RE = re.compile(r"^GET (\S+) timed out after ([\d.]+)s$")
_NET_REFUSED_RE = re.compile(r"^GET (\S+) refused: (.*)$")
_STORE_RE = re.compile(r"^(E_CONN|E_QUERY): (.*)$")
_STORE_CONN_RE = re.compile(r"^cannot connect (\S+): (.*)$")
_STORE_QUERY_RE = re.compile(r"^(.*?) failed: (.*)$")
_CACHE_RE = re.compile(r"^redis blew up on GET (\S+): (.*)$")
_SERVICE_RE = re.compile(r"^profile fetch failed for user (\S+): (.*)$")


def from_legacy(exc):
    """把遗留异常映射为等价 AppError；无法识别时返回 None。"""
    if isinstance(exc, legacy_net.NetTimeout):
        match = _NET_TIMEOUT_RE.match(str(exc))
        url, timeout = match.groups()
        return AppError("NET_TIMEOUT", cause=exc, url=url,
                        timeout=float(timeout))
    if isinstance(exc, legacy_net.NetConnRefused):
        match = _NET_REFUSED_RE.match(str(exc))
        url, detail = match.groups()
        return AppError("NET_CONN_REFUSED", cause=exc, url=url,
                        detail=detail)
    if isinstance(exc, legacy_store.StoreError):
        code, detail = _STORE_RE.match(str(exc)).groups()
        if code == "E_CONN":
            dsn = _STORE_CONN_RE.match(detail).group(1)
            return AppError("STORE_CONN", cause=exc, dsn=dsn)
        sql = _STORE_QUERY_RE.match(detail).group(1)
        return AppError("STORE_QUERY", cause=exc, sql=sql)
    if isinstance(exc, legacy_cache.CacheDown):
        key = _CACHE_RE.match(str(exc)).group(1)
        return AppError("CACHE_BACKEND_DOWN", cause=exc, key=key)
    if isinstance(exc, legacy_service.ServiceError):
        # 服务层错误是被 str() 拍平的下游错误：先还原下游，再包外层。
        user_id, inner_text = _SERVICE_RE.match(str(exc)).groups()
        inner = _from_legacy_text(inner_text)
        return AppError("PROFILE_LOAD_FAILED", cause=inner, user_id=user_id)
    return None


def _from_legacy_text(text):
    """服务层把下游异常 str() 进了消息，按既有格式尽力还原。"""
    match = _NET_TIMEOUT_RE.match(text)
    if match:
        url, timeout = match.groups()
        return AppError("NET_TIMEOUT", url=url, timeout=float(timeout))
    match = _NET_REFUSED_RE.match(text)
    if match:
        return AppError("NET_CONN_REFUSED", url=match.group(1))
    match = _STORE_RE.match(text)
    if match:
        code, detail = match.groups()
        if code == "E_CONN":
            return AppError("STORE_CONN",
                            dsn=_STORE_CONN_RE.match(detail).group(1))
        return AppError("STORE_QUERY",
                        sql=_STORE_QUERY_RE.match(detail).group(1))
    match = _CACHE_RE.match(text)
    if match:
        return AppError("CACHE_BACKEND_DOWN", key=match.group(1))
    return None
