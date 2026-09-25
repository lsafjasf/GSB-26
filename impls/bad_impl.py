"""替身/新版本实现：故意埋入 7 处与契约不一致，供框架自测检出。

埋入的不一致（覆盖异常类型、非法参数、缺失字段语义、重复调用、
超时、部分失败等场景）：
1. get 键不存在时返回 None，而不是抛 KeyNotFoundError
2. set 接受空串键，而不是抛 InvalidKeyError
3. set 非字符串键时抛内置 TypeError，异常类型与契约不符
4. 纯空白键也被接受（minor 边界）
5. delete 永远返回 True（重复删除语义错误）
6. set_many 边写边校验，失败时留下部分写入（破坏原子性）
7. size 休眠 300ms（触发超时）
"""

import time

from kv_interface import InvalidValueError


class BadKV:
    def __init__(self):
        self._data = {}

    def get(self, key):
        # 偏差 1：不抛 KeyNotFoundError
        return self._data.get(key)

    def set(self, key, value):
        # 偏差 2/3/4：不做契约规定的键校验，非字符串键触发 TypeError
        if not isinstance(key, str):
            raise TypeError("key must be str")
        if value is None:
            raise InvalidValueError("value 不能为 None")
        self._data[key] = value
        return None

    def delete(self, key):
        # 键类型校验仍保留（与契约一致），但 ——
        if not isinstance(key, str) or not key.strip():
            from kv_interface import InvalidKeyError
            raise InvalidKeyError(f"非法键: {key!r}")
        self._data.pop(key, None)
        return True  # 偏差 5：永远返回 True

    def set_many(self, pairs):
        count = 0
        for key, value in pairs.items():
            if value is None:
                raise InvalidValueError(f"value 不能为 None: {key!r}")
            self._data[key] = value  # 偏差 6：先写了部分数据才报错
            count += 1
        return count

    def size(self):
        time.sleep(0.3)  # 偏差 7：慢，超过契约超时
        return len(self._data)
