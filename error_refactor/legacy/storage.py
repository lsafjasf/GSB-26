"""重构前：存储模块，直接抛内建 IOError/OSError，errno 语义靠调用方自己懂。"""

import errno

from .errors import DiskFullError


def save(path, data, fs_write):
    """fs_write(path, data) -> None；底层 OSError 在此翻译（一部分）。"""
    try:
        fs_write(path, data)
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            raise DiskFullError("no space left when writing %s" % path)
        raise  # 其他 OSError 原样外泄，消息格式随平台而变
    return path
