"""重构后：缓存模块统一错误类型，可重试性由注册表推导。"""

from .errors import AppError


def get(key, *, backend):
    try:
        return backend(key)
    except OSError as exc:
        raise AppError("CACHE_BACKEND_DOWN", cause=exc, key=key) from exc
