"""被测接口：一个键值存储 KVStore。

接口定义（所有实现必须遵守的“契约对象”）：
    get(key)               : 返回值；键不存在抛 KeyNotFoundError；非法键抛 InvalidKeyError
    set(key, value)        : 覆盖写入，返回 None；value 为 None 抛 InvalidValueError
    delete(key)            : 删除并返回 bool（原本是否存在）；非法键抛 InvalidKeyError
    set_many(pairs: dict)  : 原子批量写入，返回写入条数；任一 value 为 None 抛
                             InvalidValueError 且不得产生部分写入
    size()                 : 当前条目数（必须快速返回）
"""


class KeyNotFoundError(Exception):
    pass


class InvalidKeyError(Exception):
    pass


class InvalidValueError(Exception):
    pass


def validate_key(key) -> None:
    """空串、纯空白、非字符串都是非法键。"""
    if not isinstance(key, str) or not key.strip():
        raise InvalidKeyError(f"非法键: {key!r}")
