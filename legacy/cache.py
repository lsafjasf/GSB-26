"""重构前：缓存模块又一套风格，异常里只有 repr 拼出的文本。"""


class CacheDown(Exception):
    pass


def get(key, *, backend):
    try:
        return backend(key)
    except OSError as exc:
        raise CacheDown("redis blew up on GET %s: %r" % (key, exc))
