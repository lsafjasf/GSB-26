"""重构前：网络模块，错误风格 = 拼字符串 + 自造异常。"""

import socket

from .errors import ConnectFailure, HttpError


def fetch(host, port, path, transport):
    """transport(host, port, path) -> (status, body)；底层异常在此翻译。"""
    url = "http://%s:%d%s" % (host, port, path)
    try:
        status, body = transport(host, port, path)
    except socket.timeout:
        raise ConnectFailure("connect to %s:%d timed out" % (host, port))
    except ConnectionRefusedError:
        raise ConnectFailure("connect to %s:%d refused" % (host, port))
    if status >= 500:
        raise HttpError(status, url)
    if status >= 400:
        raise HttpError(status, url)
    return body
