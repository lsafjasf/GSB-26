"""替身实现 stub：行为基本对齐，仅存在 minor 级差异。

- 不记录 audit 副作用
- 大整数经 float 处理，丢失精度
"""
from .impl_v1 import _is_num


class ImplStub:
    name = "stub-testdouble"

    def call(self, op, payload, ctx):
        if op == "add":
            a, b = payload["a"], payload["b"]
            if not _is_num(a) or not _is_num(b):
                raise TypeError("add 需要数字参数")
            return float(a) + float(b)  # 大整数丢失精度
        if op == "transfer":
            return payload["amount"]  # 不 emit audit
        if op == "div":
            a, b = payload["a"], payload["b"]
            if b == 0:
                raise ZeroDivisionError("除数为零")
            return a / b
        if op == "compute":
            return sum(range(payload["n"] + 1))
        if op == "balance":
            return 100
        if op == "batch_div":
            results, errors = [], []
            for index, pair in enumerate(payload["pairs"]):
                a, b = pair
                if b == 0:
                    errors.append({"index": index, "error": "ZeroDivisionError"})
                else:
                    results.append(a / b)
            return {"results": results, "errors": errors}
        raise ValueError("未知操作 %r" % op)
