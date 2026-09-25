# 模块头部注释第一行
# 模块头部注释第二行

import os  # 模块级行尾注释


def beta():
    pass
    # beta 的尾部说明：缩进深，属于 beta 而非 gamma


# gamma 的前置说明
def gamma():  # gamma 定义行的行尾注释
    # gamma 内部注释
    return 1


def delta():
    pass

# epsilon 的说明第一行
# epsilon 的说明第二行
def epsilon():
    pass


# 被装饰函数的说明
@staticmethod
@decorator(arg=1)  # 装饰器行尾注释
def decorated(value):
    """正文。"""
    pass


class Klass:
    # 方法的前置说明
    def method(self):
        # 方法内部注释
        pass

    def other(self):
        pass
        # other 的尾部说明，属于 other


def outer():
    # 嵌套函数的说明
    def inner():
        pass

    return inner


async def fetch(url):
    # 异步函数内部注释
    return url
