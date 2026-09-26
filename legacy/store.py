"""重构前：存储模块把错误码塞进消息字符串，调用方只能正则解析。"""


class StoreError(Exception):
    """消息格式约定为 "<CODE>: <detail>"，无结构化字段。"""


def connect(dsn, *, dial):
    try:
        return dial(dsn)
    except OSError as exc:
        raise StoreError("E_CONN: cannot connect %s: %s" % (dsn, exc))


def query(conn, sql, params=()):
    try:
        return conn.execute(sql, params)
    except DeadlockError as exc:
        raise StoreError("E_QUERY: %s failed: %s" % (sql, exc))


class DeadlockError(Exception):
    pass
