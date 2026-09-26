"""重构前：网络模块自造错误类型，消息为自由拼接文本。"""


class NetError(Exception):
    pass


class NetTimeout(NetError):
    pass


class NetConnRefused(NetError):
    pass


def http_get(url, *, transport, timeout=3.0):
    """transport(url, timeout) -> bytes，由调用方注入，便于测试复现故障。"""
    try:
        return transport(url, timeout)
    except TimeoutError:
        raise NetTimeout("GET %s timed out after %.1fs" % (url, timeout))
    except ConnectionRefusedError as exc:
        raise NetConnRefused("GET %s refused: %s" % (url, exc))
