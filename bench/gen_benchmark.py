#!/usr/bin/env python3
"""生成约 10 万行的合成 Python 代码库用于性能测量。

用法：python3 bench/gen_benchmark.py [目标行数] [输出文件]
"""

import sys

TEMPLATE_FUNC = '''\
# 函数 {i} 的前置文档注释
# 第二行说明
def func_{i}(alpha, beta={i}, gamma="s{i}"):
    """函数 {i} 的文档字符串。

    Args:
        alpha (int): 第一个参数
        beta (int): 第二个参数，默认 {i}
        gamma (str): 第三个参数

    Returns:
        int: 计算结果
    """
    # 内部注释：准备数据
    data = [alpha, beta]  # 行尾注释
    total = 0
    for idx, value in enumerate(data):
        # 循环内注释
        total += value * idx
    return total  # 返回


'''

TEMPLATE_CLASS = '''\
class Klasse{i}:
    """类 {i} 的文档。"""

    # 方法 {i} 的文档
    def method_{i}(self, x):
        """方法文档。

        :param x: 输入值
        :type x: int
        :return: 输出值
        :rtype: int
        """
        # 方法内注释
        return x + {i}


'''


def generate(target_lines=100_000):
    chunks, lines, i = [], 0, 0
    chunks.append('"""基准测试合成模块。"""\n\n')
    lines += 3
    while lines < target_lines:
        block = TEMPLATE_FUNC.format(i=i)
        chunks.append(block)
        lines += block.count("\n")
        if i % 5 == 0:
            block = TEMPLATE_CLASS.format(i=i)
            chunks.append(block)
            lines += block.count("\n")
        i += 1
    return "".join(chunks)


def main():
    target = int(sys.argv[1]) if len(sys.argv) > 1 else 100_000
    out = sys.argv[2] if len(sys.argv) > 2 else "bench/bench_src.py"
    source = generate(target)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(source)
    print("generated {} -> {} lines".format(out, source.count("\n") + 1))


if __name__ == "__main__":
    main()
