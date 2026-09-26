"""重构前：鉴权模块，过期/拒绝混在同一个 AuthException 里，靠文本区分。"""

from .errors import AuthException, BadInput


def authenticate(token, verifier):
    """verifier(token) -> claims；底层异常在此翻译。"""
    if not token:
        raise BadInput("token must not be empty")
    try:
        return verifier(token)
    except TokenExpired:
        raise AuthException("token expired")
    except PermissionError:
        raise AuthException("forbidden: insufficient scope")


class TokenExpired(Exception):
    """verifier 底层可能抛出的过期信号。"""
