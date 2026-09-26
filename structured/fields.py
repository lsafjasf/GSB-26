"""全部事件类型的声明式定义。

============================================================
新增一个事件类型需要改动的唯一位置就是本文件：
在列表中追加一个 EventSchema（事件名、分级、字段声明）即可，
框架（event.py / logger.py / masking.py）与业务代码无需任何改动。
============================================================
"""

from .event import EventSchema, Field

SCHEMAS = {s.name: s for s in [
    EventSchema("user_logged_in", "INFO", (
        Field("user_id", str),
        Field("email", str, mask="email"),
        Field("ip", str),
    )),
    EventSchema("order_created", "INFO", (
        Field("order_id", str),
        Field("user_id", str),
        Field("total", (int, float)),
        Field("phone", str, mask="phone"),
    )),
    EventSchema("payment_succeeded", "INFO", (
        Field("order_id", str),
        Field("amount", (int, float)),
        Field("card_no", str, mask="card"),
    )),
    EventSchema("payment_failed", "ERROR", (
        Field("order_id", str),
        Field("expected", (int, float)),
        Field("got", (int, float)),
    )),
    EventSchema("order_refunded", "WARN", (
        Field("order_id", str),
        Field("amount", (int, float)),
        Field("reason", str),
    )),
    EventSchema("settlement_failed", "ERROR", (
        Field("order_id", str),
        # 异常详情通过 emit(..., error=exc) 的可选错误信息携带
    )),
]}
