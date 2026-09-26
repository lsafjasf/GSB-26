"""重写实现 v2（新版本）：故意引入多处与 v1 不一致的行为。

- add 对字符串参数做静默转换（应为 TypeError）
- 缺字段时抛 ValueError（应为 KeyError）
- compute 响应过慢（超时）
- balance 非幂等（每次递减）
- batch_div 遇到坏数据整体抛错（应返回部分结果）
"""
import time

from .impl_v1 import _is_num


class ImplV2:
    name = "v2-rewrite"

    def __init__(self):
        self._balance = 100

    def call(self, op, payload, ctx):
        if op == "add":
            if "a" not in payload or "b" not in payload:
                raise ValueError("缺少参数")
            a = float(payload["a"]) if isinstance(payload["a"], str) else payload["a"]
            b = float(payload["b"]) if isinstance(payload["b"], str) else payload["b"]
            if not _is_num(a) or not _is_num(b):
                raise TypeError("add 需要数字参数")
            return a + b
        if op == "transfer":
            amount = payload["amount"]
            ctx.emit("audit", "transfer(%r)" % amount)
            return amount
        if op == "div":
            a, b = payload["a"], payload["b"]
            if b == 0:
                raise ZeroDivisionError("除数为零")
            return a / b
        if op == "compute":
            time.sleep(1.0)  # 性能退化
            return sum(range(payload["n"] + 1))
        if op == "balance":
            self._balance -= 1  # 非幂等：读操作改变了状态
            return self._balance
        if op == "batch_div":
            results = []
            for pair in payload["pairs"]:
                a, b = pair
                if b == 0:
                    raise ZeroDivisionError("除数为零")  # 整体失败而非部分失败
                results.append(a / b)
            return {"results": results, "errors": []}
        raise ValueError("未知操作 %r" % op)
