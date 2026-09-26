"""重构后：鉴权模块，过期与拒绝拆成不同错误码，不再靠文本区分。"""

from .errors import AppError


def authenticate(token, verifier):
    if not token:
        raise AppError("VALIDATION_EMPTY_TOKEN")
    try:
        return verifier(token)
    except TokenExpired as exc:
        raise AppError("AUTH_TOKEN_EXPIRED") from exc
    except PermissionError as exc:
        raise AppError("AUTH_FORBIDDEN") from exc


class TokenExpired(Exception):
    """verifier 底层可能抛出的过期信号（与 legacy 保持同一底层契约）。"""
