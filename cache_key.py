"""修复后的缓存键规范化实现（仅标准库）。

规范化规则（与 NORMALIZATION.md 一致）：
1. 过滤不稳定字段：键名（不区分大小写）属于 VOLATILE_FIELDS 的参数被移除，
   递归作用于嵌套 dict。
2. 键名规范化：去除首尾空白并转小写。
3. 空值省略：None、空字符串、空 list、空 dict 的参数被移除（递归）。
4. 默认值省略：值等于 defaults 中对应键的默认值时移除（递归）。
5. 类型规范化：bool -> "true"/"false"；整数语义的 int/float -> 十进制整数字符串；
   其余 float -> repr；str 保持原样（大小写敏感，大小写属于语义）。
6. 排序：dict 按键名排序（递归）；list 保持顺序（顺序属于语义）。
7. 序列化：json.dumps(sort_keys=True, ensure_ascii=False, separators=(",", ":"))，
   无分隔符注入歧义。
8. 长键处理：canonical 串 UTF-8 超过 MAX_KEY_BYTES 时，键为
   "sha256:" + 完整 canonical 串的 SHA-256；绝不截断。
"""

import hashlib
import json

MAX_KEY_BYTES = 200

VOLATILE_FIELDS = frozenset({
    "timestamp", "ts", "time", "request_id", "req_id", "trace_id",
    "nonce", "sign", "signature", "_",
})

_EMPTY = (type(None),)


def _is_empty(value):
    return value is None or value == "" or value == [] or value == {}


def _normalize_value(value, defaults):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return [_normalize_value(v, None) for v in value]
    if isinstance(value, dict):
        return _normalize_dict(value, defaults)
    raise TypeError("unsupported param type: {}".format(type(value).__name__))


def _normalize_dict(params, defaults):
    out = {}
    for raw_key, value in params.items():
        key = str(raw_key).strip().lower()
        if key in VOLATILE_FIELDS:
            continue
        if _is_empty(value):
            continue
        sub_defaults = None
        if isinstance(defaults, dict):
            sub_defaults = defaults.get(key)
            if key in defaults and not isinstance(defaults[key], dict):
                if _normalize_value(value, None) == _normalize_value(
                    defaults[key], None
                ):
                    continue
                sub_defaults = None
        normalized = _normalize_value(value, sub_defaults)
        if _is_empty(normalized):  # 归约后为空（如默认值全部被省略）则剪除
            continue
        out[key] = normalized
    return out


def canonicalize(params, defaults=None):
    """返回规范化后的 canonical 字符串（语义等价 -> 字符串相同）。"""
    normalized = _normalize_dict(params or {}, defaults)
    return json.dumps(
        normalized, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )


def make_key(params, defaults=None):
    """根据请求参数生成缓存键（修复版本）。

    性质：
    - 语义相同 -> 键相同（排序/空值/默认值/不稳定字段均已规范化）。
    - 语义不同 -> 键不同：短键即 canonical 串本身；长键使用完整内容的
      SHA-256，不截断，碰撞概率可忽略（2^-128 生日界）。
    """
    canonical = canonicalize(params, defaults)
    raw = canonical.encode("utf-8")
    if len(raw) <= MAX_KEY_BYTES:
        return "v1:" + canonical
    return "sha256:" + hashlib.sha256(raw).hexdigest()
