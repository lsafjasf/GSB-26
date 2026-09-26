"""演示多层包装：服务层在模块错误之上再包一层业务语义，原因链不断。"""

from .errors import AppError
from . import net_client, storage


def fetch_and_cache(host, port, path, cache_path, transport, fs_write):
    try:
        body = net_client.fetch(host, port, path, transport)
    except AppError as exc:
        raise AppError("INTERNAL_UNEXPECTED", retryable=exc.retryable,
                       detail="fetch %s failed" % path) from exc
    storage.save(cache_path, body, fs_write)
    return body
