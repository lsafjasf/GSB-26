"""重构后：存储模块，所有 OSError 都收敛为 AppError，不再原样外泄。"""

import errno

from .errors import AppError


def save(path, data, fs_write):
    try:
        fs_write(path, data)
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            raise AppError("STORAGE_DISK_FULL", path=path) from exc
        raise AppError("STORAGE_IO", path=path, errno=exc.errno) from exc
    return path
