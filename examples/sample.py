"""示例模块：包含直接递归、相互递归、动态分派与外部调用。"""

import os


def factorial(n):
    """直接递归（自环）。"""
    return 1 if n <= 1 else n * factorial(n - 1)


def is_even(n):
    """与 is_odd 相互递归。"""
    return True if n == 0 else is_odd(n - 1)


def is_odd(n):
    return False if n == 0 else is_even(n - 1)


class Worker:
    def run(self):
        self.step()

    def step(self):
        kick(self)          # 经动态分派回到 run，形成潜在环


def kick(obj):
    obj.run()               # obj 类型未知 -> 不确定边（候选：Worker.run）


def runner(callback):
    callback()              # 参数调用 -> 不确定边（未知目标）


def main():
    print(factorial(5))     # print: 外部调用
    runner(factorial)
    os.getcwd()             # 外部调用
