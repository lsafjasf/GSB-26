"""参考实现：严格满足契约（代表“旧版本/真实服务”）。"""

import time

from kv_interface import (
    InvalidValueError,
    validate_key,
)

_MISSING = object()


class GoodKV:
    def __init__(self):
        self._data = {}

    def get(self, key):
        validate_key(key)
        if key not in self._data:
            from kv_interface import KeyNotFoundError
            raise KeyNotFoundError(key)
        return self._data[key]

    def set(self, key, value):
        validate_key(key)
        if value is None:
            raise InvalidValueError("value 不能为 None")
        self._data[key] = value
        return None

    def delete(self, key):
        validate_key(key)
        existed = self._data.pop(key, _MISSING) is not _MISSING
        return existed

    def set_many(self, pairs):
        # 先整体校验，再一次性写入 —— 原子性
        for key, value in pairs.items():
            validate_key(key)
            if value is None:
                raise InvalidValueError(f"value 不能为 None: {key!r}")
        self._data.update(pairs)
        return len(pairs)

    def size(self):
        time.sleep(0.001)  # 快速返回
        return len(self._data)
