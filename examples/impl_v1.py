"""参考实现 v1（旧版本）：满足全部契约点。"""


def _is_num(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class ImplV1:
    name = "v1-legacy"

    def call(self, op, payload, ctx):
        if op == "add":
            a, b = payload["a"], payload["b"]
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
