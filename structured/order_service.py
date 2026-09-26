"""重构后：业务逻辑与 legacy/order_service.py 完全一致，日志统一走结构化事件。"""

from .logger import emit


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
        emit("user_logged_in", user_id=user_id, email=email, ip=ip)

    def create_order(self, user_id, items, phone):
        self._seq += 1
        order_id = f"O{self._seq:06d}"
        total = sum(price * qty for _, price, qty in items)
        self.orders[order_id] = {"user_id": user_id, "total": total, "status": "CREATED"}
        emit("order_created", order_id=order_id, user_id=user_id, total=total, phone=phone)
        return order_id

    def pay(self, order_id, amount, card_no):
        order = self.orders[order_id]
        if amount != order["total"]:
            emit("payment_failed", order_id=order_id, expected=order["total"], got=amount)
            raise OrderError("amount mismatch")
        order["status"] = "PAID"
        emit("payment_succeeded", order_id=order_id, amount=amount, card_no=card_no)

    def refund(self, order_id, amount, reason):
        order = self.orders[order_id]
        order["status"] = "REFUNDED"
        emit("order_refunded", order_id=order_id, amount=amount, reason=reason)

    def settle(self, order_id):
        try:
            _charge_gateway(order_id)
        except Exception as exc:
            emit("settlement_failed", order_id=order_id, error=exc)


if __name__ == "__main__":
    svc = OrderService()
    svc.login("u1001", "alice@example.com", "10.0.0.8")
    oid = svc.create_order("u1001", [("apple", 3, 2), ("pear", 5, 1)], "13812345678")
    svc.pay(oid, 11, "6222020200112233")
    try:
        svc.pay(oid, 10, "6222020200112233")
    except OrderError:
        pass
    svc.refund(oid, 11, "user request")
    svc.settle(oid)
