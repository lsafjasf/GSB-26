"""缓存键规范化（修复版）。

规范化规则（语义相同 -> 同键；语义不同 -> 不同键）：

1. 参数名：按 Unicode 大小写折叠（casefold）后比较，键不区分大小写；
   折叠后重名的参数视为歧义，抛出 ValueError，避免静默合并。
2. 排序：每一层映射按键名 casefold 后的码点升序排序，消除参数顺序影响。
3. 空值：null(None)、空字符串("")、空容器([]/{}) 是四种不同语义，
   类型被显式编码，绝不互相合并；键不存在与值为 null 也不同。
4. 默认值省略：显式等于该键声明默认值的参数不参与键（省略它与传入
   默认值语义相同）；默认值在规范化之前判定，受下面的不稳定字段规则
   与大小写规则约束。
5. 不稳定字段：时间戳、请求标识等每次调用都变化的字段不参与键，
   见 DEFAULT_VOLATILE_KEYS；可用 volatile_keys 覆盖。
6. 结构保真：列表有序；元组按列表处理（HTTP 参数没有元组类型）；
   集合先对每个元素做规范化再排序（无序容器需确定化）；
   标量类型保真，1/"1"/true 是不同的键，防止类型碰撞。
7. 分隔/注入：使用 JSON(canonical JSON) 序列化，键值边界由语法结构
   保证，"a=b&c" 这类字符串无法伪造嵌套结构。
8. Unicode：字符串统一 NFC，非 ASCII 按原值参与，不转义、不归一化为 ?。
9. 超长键：先用完整 canonical JSON 保证无碰撞，再做 SHA-256 摘要压缩
   长度（键永远不会被截断）；ParamCache 取键时同时比较完整规范化串，
   即使摘要碰撞也不会把不同请求合并。

只依赖 Python 标准库。
"""

import hashlib
import json
import unicodedata
from collections.abc import Mapping, Set

CANONICAL_VERSION = "ck2"

DEFAULT_VOLATILE_KEYS = frozenset({
    "timestamp", "ts", "requestid", "request_id", "reqid", "traceid",
    "trace_id", "nonce", "correlationid", "correlation_id",
})

_UNDEF = object()


def _normalize(value, on_unsupported="error"):
    if value is None:
        return {"t": "null"}
    if isinstance(value, str):
        return {"t": "str", "v": unicodedata.normalize("NFC", value)}
    if isinstance(value, bool):
        return {"t": "bool", "v": value}
    if isinstance(value, int):
        return {"t": "int", "v": value}
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("non-finite floats are not canonicalizable")
        return {"t": "float", "v": value}
    if isinstance(value, Mapping):
        entries = []
        seen = set()
        for name in sorted(value, key=_fold):
            folded = _fold(name)
            if folded in seen:
                raise ValueError(
                    "parameter names collide after case folding: "
                    f"{folded!r}"
                )
            seen.add(folded)
            normalized = _normalize(value[name], on_unsupported)
            if normalized is not _UNDEF:
                entries.append((folded, normalized))
        return {"t": "map", "v": dict(entries)}
    if isinstance(value, (list, tuple)):
        elements = []
        for item in value:
            normalized = _normalize(item, on_unsupported)
            if normalized is not _UNDEF:
                elements.append(normalized)
        return {"t": "list", "v": elements}
    if isinstance(value, Set):
        elements = sorted(
            json.dumps(_normalize(item, on_unsupported),
                       ensure_ascii=False, sort_keys=True)
            for item in value
        )
        return {"t": "set", "v": [json.loads(e) for e in elements]}
    if on_unsupported == "drop":
        return _UNDEF
    raise ValueError(f"unsupported value type for cache key: {type(value)!r}")


def _fold(name):
    if not isinstance(name, str):
        raise ValueError("parameter names must be strings")
    return unicodedata.normalize("NFC", name).casefold()


def _canonicalize_top_level(params, on_unsupported, volatile_keys, defaults):
    entries = []
    seen = set()
    for raw_name, raw_value in params.items():
        folded = _fold(raw_name)
        if folded in seen:
            raise ValueError(
                f"parameter names collide after case folding: {folded!r}"
            )
        seen.add(folded)
        entries.append((folded, raw_name, raw_value))

    result = {}
    for folded, raw_name, raw_value in sorted(entries, key=lambda e: e[0]):
        if folded in volatile_keys:
            continue
        if defaults is not None and raw_value == defaults.get(
                raw_name, defaults.get(folded, _UNDEF)):
            continue
        normalized = _normalize(raw_value, on_unsupported)
        if normalized is not _UNDEF:
            result[folded] = normalized
    return result


def canonicalize(params, *, volatile_keys=DEFAULT_VOLATILE_KEYS,
                 defaults=None, on_unsupported="error"):
    """返回规范化后的 canonical JSON 字符串（完整、确定、无截断）。"""
    if params is None:
        params = {}
    if not isinstance(params, Mapping):
        raise ValueError("params must be a mapping")
    volatile_keys = {_fold(key) for key in volatile_keys}
    body = _canonicalize_top_level(
        params, on_unsupported, volatile_keys, defaults,
    )
    return json.dumps(body, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def digest(params, *, volatile_keys=DEFAULT_VOLATILE_KEYS, defaults=None,
           on_unsupported="error"):
    """返回长度固定、永不截断的键：版本前缀 + SHA-256(canonical JSON)。"""
    canonical = canonicalize(
        params, volatile_keys=volatile_keys, defaults=defaults,
        on_unsupported=on_unsupported,
    )
    h = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{CANONICAL_VERSION}:{h}"


class ParamCache:
    """抗碰撞参数缓存。

    桶 id 是 canonical JSON 的摘要（定长、不截断）；桶内保存完整
    canonical 串，命中前必须完整相等，因此即使发生摘要碰撞，
    语义不同的请求也不会被合并。
    """

    def __init__(self, *, volatile_keys=DEFAULT_VOLATILE_KEYS,
                 defaults=None, on_unsupported="error"):
        self._volatile_keys = volatile_keys
        self._defaults = defaults
        self._on_unsupported = on_unsupported
        self._buckets = {}

    def _resolve(self, params):
        canonical = canonicalize(
            params,
            volatile_keys=self._volatile_keys,
            defaults=self._defaults,
            on_unsupported=self._on_unsupported,
        )
        key = digest(
            params,
            volatile_keys=self._volatile_keys,
            defaults=self._defaults,
            on_unsupported=self._on_unsupported,
        )
        return key, canonical

    def set(self, params, value):
        key, canonical = self._resolve(params)
        self._buckets.setdefault(key, {})[canonical] = value
        return key

    def get(self, params, default=None):
        key, canonical = self._resolve(params)
        bucket = self._buckets.get(key)
        if bucket is None:
            return default
        return bucket.get(canonical, default)

    def __len__(self):
        return sum(len(bucket) for bucket in self._buckets.values())

    def entries_for(self, params):
        """测试辅助：查看某摘要桶内保存了几个不同的规范串。"""
        key, _ = self._resolve(params)
        return len(self._buckets.get(key, {}))
