def process(items):
    """处理列表。"""
    # 第一步：过滤空值
    result = [x for x in items if x]  # 过滤
    # 第二步：排序
    result.sort()
    return result
