"""原始（有缺陷）的缓存键规范化实现。

已知缺陷（对应现网四类问题的根因）：
1. 不排序：按 dict 插入顺序拼接，参数顺序不同 -> 键不同 -> 误未命中。
2. 不转义：用 "k=v" + "&" 直接拼接，值里含 "&"/"=" 时与真实多参数
   文本相同 -> 语义不同的请求共享键 -> 互相污染。
3. 截断：键超过 64 字符直接截断 -> 前缀相同的超长参数请求碰撞。
4. 不过滤不稳定字段：timestamp / request_id 等参与键 -> 命中率极低。
"""

MAX_KEY_LEN = 64


def make_key(params):
    """根据请求参数生成缓存键（有缺陷版本）。"""
    if not params:
        return ""
    parts = []
    for k, v in params.items():  # 缺陷1：不排序，依赖插入顺序
        if v is None:
            continue
        # 缺陷2：不转义，值中的 & 和 = 会污染键结构
        parts.append("{}={}".format(k, v))
    key = "&".join(parts)  # 缺陷4：timestamp/request_id 等全部参与
    return key[:MAX_KEY_LEN]  # 缺陷3：直接截断，前缀相同即碰撞
