"""重构后：网络模块，统一抛 AppError，底层异常进原因链。"""

import socket

from .errors import AppError


def fetch(host, port, path, transport):
    url = "http://%s:%d%s" % (host, port, path)
    try:
        status, body = transport(host, port, path)
    except socket.timeout as exc:
        raise AppError("NET_CONNECT_TIMEOUT", host=host, port=port) from exc
    except ConnectionRefusedError as exc:
        raise AppError("NET_CONNECT_REFUSED", host=host, port=port) from exc
    if status >= 500:
        raise AppError("NET_HTTP_5XX", status=status, url=url)
    if status >= 400:
        raise AppError("NET_HTTP_4XX", status=status, url=url)
    return body
