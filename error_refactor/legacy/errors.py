"""重构前：各模块自造错误类型，消息靠自由文本拼接。"""


class ConnectFailure(Exception):
    """net_client 自造：连接失败，信息全在 str(e) 里。"""


class HttpError(Exception):
    """net_client 自造：HTTP 非 2xx。"""

    def __init__(self, status, url):
        self.status = status
        self.url = url
        super().__init__("bad response %d from %s" % (status, url))


class DiskFullError(IOError):
    """storage 自造：磁盘满。"""


class AuthException(Exception):
    """auth 自造：鉴权失败，原因靠解析消息文本。"""


class BadInput(Exception):
    """validation 自造：入参不合法。"""
