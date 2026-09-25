"""近似实现：与契约仅有一处 minor 级边界差异（接受纯空白键），
用于验证“有条件替换”判定，避免误报为不可替换。"""

from kv_interface import InvalidKeyError, InvalidValueError

_MISSING = object()


class MinorKV:
    def __init__(self):
        self._data = {}

    def _validate(self, key):
        # 仅拒绝非字符串和空串；纯空白键被接受（与契约的 strip 校验不同）
        if not isinstance(key, str) or key == "":
            raise InvalidKeyError(f"非法键: {key!r}")

    def get(self, key):
        self._validate(key)
        if key not in self._data:
            from kv_interface import KeyNotFoundError
            raise KeyNotFoundError(key)
        return self._data[key]

    def set(self, key, value):
        self._validate(key)
        if value is None:
            raise InvalidValueError("value 不能为 None")
        self._data[key] = value
        return None

    def delete(self, key):
        self._validate(key)
        existed = self._data.pop(key, _MISSING) is not _MISSING
        return existed

    def set_many(self, pairs):
        for key, value in pairs.items():
            self._validate(key)
            if value is None:
                raise InvalidValueError(f"value 不能为 None: {key!r}")
        self._data.update(pairs)
        return len(pairs)

    def size(self):
        return len(self._data)
