def google_style(name, count, flag=True):
    """Google 风格示例。

    Args:
        name (str): 名称
        count (int): 重复次数，
            支持多行描述
        flag: 是否启用

    Returns:
        str: 拼接结果

    Raises:
        ValueError: 参数非法时抛出
    """
    return name * count


def numpy_style(x, y):
    """NumPy 风格示例。

    Parameters
    ----------
    x : float
        横坐标
    y : float
        纵坐标

    Returns
    -------
    float
        到原点的距离平方
    """
    return x * x + y * y

def sphinx_style(path, mode):
    """Sphinx 风格示例。

    :param path: 文件路径
    :type path: str
    :param mode: 打开模式
    :return: 文件内容
    :rtype: str
    :raises IOError: 读取失败
    """
    return path + mode

def malformed(a, b):
    """格式不匹配的行要保留原文。

    Args:
        a (int): 正常条目
        这一行没有遵循 name: desc 格式
        - 也不是列表项格式

    Returns:
        没有类型前缀的自由文本返回说明
    """
    return a
