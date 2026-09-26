"""重构前：日志靠字符串拼接，格式五花八门，字段名不统一。"""

import time


def _ts():
    return time.strftime("%Y-%m-%d %H:%M:%S")


class OrderError(Exception):
    pass


def _charge_gateway(order_id):
    # 模拟下游支付网关调用失败
    raise TimeoutError("gateway timeout")


class OrderService:
    def __init__(self):
        self.orders = {}
        self._seq = 0

    def login(self, user_id, email, ip):
        print(f"LOGIN uid={user_id} email={email} ip={ip}")

    def create_order(self, user_id, items, phone):
        self._seq += 1
        order_id = f"O{self._seq:06d}"
        total = sum(price * qty for _, price, qty in items)
        self.orders[order_id] = {"user_id": user_id, "total": total, "status": "CREATED"}
        print(f"{_ts()} [ORDER] create ok uid={user_id} oid={order_id} total={total} phone={phone}")
        return order_id

    def pay(self, order_id, amount, card_no):
        order = self.orders[order_id]
        if amount != order["total"]:
            print(f"PAY FAIL! order {order_id} expect={order['total']} got={amount}")
            raise OrderError("amount mismatch")
        order["status"] = "PAID"
        print(f"{_ts()} pay success | order:{order_id} | amount:{amount} | card:{card_no}")

    def refund(self, order_id, amount, reason):
        order = self.orders[order_id]
        order["status"] = "REFUNDED"
        print(f"{_ts()} WARN refund>> oid={order_id} amount={amount} reason={reason}")

    def settle(self, order_id):
        try:
            _charge_gateway(order_id)
        except Exception as exc:
            print(f"{_ts()} ERROR settle order={order_id} failed: {type(exc).__name__}: {exc}")
