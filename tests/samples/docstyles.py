def rest_style(name, count):
    """reST 风格示例。

    :param name: 名称。
    :type name: str
    :param count: 次数。
    :type count: int
    :returns: 拼接结果。
    :rtype: str
    """
    return name * count


def google_style(path, mode="r", *flags):
    """Google 风格示例。

    第二段正文。

    Args:
        path (str): 文件路径。
        mode (str): 打开模式，
            支持续行说明。
        *flags: 额外标记。
        这一行不符合参数格式

    Returns:
        bool: 是否成功。
    """
    return True


def numpy_style(data, axis):
    """NumPy 风格示例。

    Parameters
    ----------
    data : list of int
        输入数据。
    axis : int
        轴编号。
    不符合格式的行

    Returns
    -------
    int
        合计值。
    """
    return sum(data)


def plain_doc():
    """没有任何结构化字段的普通文档。"""
    pass


def no_doc(a):
    return a
