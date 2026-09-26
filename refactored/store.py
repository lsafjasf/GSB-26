"""重构后：存储模块不再把错误码塞进消息字符串。"""

from .errors import AppError


class DeadlockError(Exception):
    pass


def connect(dsn, *, dial):
    try:
        return dial(dsn)
    except OSError as exc:
        raise AppError("STORE_CONN", cause=exc, dsn=dsn) from exc


def query(conn, sql, params=()):
    try:
        return conn.execute(sql, params)
    except DeadlockError as exc:
        raise AppError("STORE_QUERY", cause=exc, sql=sql) from exc
