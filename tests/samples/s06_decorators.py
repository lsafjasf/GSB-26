def deco(fn):
    return fn


# 属于 wrapped 的文档（在装饰器之上）
@deco
@deco
def wrapped(a):
    return a


@deco
# 装饰器与 def 之间的注释也算 leading
def wrapped2(a):
    return a


@deco  # 装饰器行尾注释
def wrapped3(a):
    return a
