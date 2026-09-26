"""旧版缓存键实现（保留用于复现现网缺陷与修复前后对比）。

四类已知缺陷：
1. 参数按字典拼接顺序直接序列化，插入顺序变化 -> 键变化 -> 缓存未命中。
2. 键值拼接没有分隔/转义，a=["x"] 与 ["a=x"] 这类语义不同的请求文本相撞。
3. 超过 MAX_KEY_LEN 直接截断，超长参数被截断后互相碰撞。
4. 时间戳、请求标识等不稳定字段无条件参与键，命中率极低。
"""

MAX_KEY_LEN = 64


def _flatten(params, prefix=""):
    parts = []
    for name, value in params.items():
        key = f"{prefix}.{name}" if prefix else name
        if isinstance(value, dict):
            parts.extend(_flatten(value, key))
        elif isinstance(value, (list, tuple)):
            for item in value:
                parts.append(f"{key}={item}")
        else:
            parts.append(f"{key}={value}")
    return parts


def make_cache_key(params):
    if not params:
        return "req?"
    return "&".join(_flatten(params))[:MAX_KEY_LEN]
