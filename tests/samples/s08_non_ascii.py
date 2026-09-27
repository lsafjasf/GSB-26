# -*- coding: utf-8 -*-
"""模块说明：支持中文、日文、émojis 🎉。"""

# 问候函数：根据名字生成问候语
def greet(名前, 言語="中文"):
    """生成问候语。

    Args:
        名前 (str): 用户名字
        言語 (str): 问候语言，默认中文

    Returns:
        str: 问候语，例如「你好，世界」
    """
    return "你好，{}".format(名前)
