"""重构后：网络模块统一抛出 AppError，上下文结构化。"""

from .errors import AppError


def http_get(url, *, transport, timeout=3.0):
    try:
        return transport(url, timeout)
    except TimeoutError as exc:
        raise AppError("NET_TIMEOUT", cause=exc, url=url,
                       timeout=timeout) from exc
    except ConnectionRefusedError as exc:
        raise AppError("NET_CONN_REFUSED", cause=exc, url=url) from exc
