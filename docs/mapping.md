# 重构前后字段对照表与统计

## 1. 字段对照表（旧字符串日志 → 结构化事件）

| 业务路径 | 重构前日志样例 | 重构后事件 | 旧日志片段 → 新字段 | 脱敏 |
|---|---|---|---|---|
| 登录 | `LOGIN uid=u1001 email=alice@example.com ip=10.0.0.8` | `user_logged_in` (INFO) | `uid`→`user_id`，`email`→`email`，`ip`→`ip` | `email`: email 规则 |
| 创建订单 | `[ORDER] create ok uid=u1001 oid=O000001 total=11 phone=13812345678` | `order_created` (INFO) | `uid`→`user_id`，`oid`→`order_id`，`total`→`total`，`phone`→`phone` | `phone`: phone 规则 |
| 支付成功 | `pay success \| order:O000001 \| amount:11 \| card:6222020200112233` | `payment_succeeded` (INFO) | `order`→`order_id`，`amount`→`amount`，`card`→`card_no` | `card_no`: card 规则 |
| 支付失败 | `PAY FAIL! order O000001 expect=11 got=10` | `payment_failed` (ERROR) | `order`→`order_id`，`expect`→`expected`，`got`→`got` | — |
| 退款 | `WARN refund>> oid=O000001 amount=11 reason=user request` | `order_refunded` (WARN) | `oid`→`order_id`，`amount`→`amount`，`reason`→`reason` | — |
| 结算失败 | `ERROR settle order=O000001 failed: TimeoutError: gateway timeout` | `settlement_failed` (ERROR) | `order`→`order_id`，`TimeoutError`→`error.type`，`gateway timeout`→`error.message`（另附 `error.stack` 完整堆栈） | — |

等价性由 `tests/test_equivalence.py` 对拍保证：同一业务路径各跑一遍，
旧日志解析出的每个关键值都与新事件对应字段逐一相等（含 error.type / error.message）。

说明：事件对象内部保留原始值（信息零丢失），脱敏只发生在 `LogEvent.render()`
输出时；脱敏规则全部集中在 `structured/masking.py` 的 `RULES` 中。

## 2. 新增一个事件类型需要改动的唯一位置

只改 `structured/fields.py`：在列表中追加一个 `EventSchema`（事件名、分级、
字段声明）即可。框架（`event.py` / `logger.py` / `masking.py`）无需改动；
业务代码随后直接 `emit("新事件名", ...)`，缺字段/类型不符会在调用处报错并
指出文件与行号。若需新脱敏规则，在 `structured/masking.py` 的 `RULES`
中加一个函数（这也是脱敏逻辑唯一允许出现的位置）。

## 3. 代码行数与埋点数量对比（`python3 tools/stats.py` 自动生成）

| | 总行数 | 代码行 | 埋点行数 | 埋点数量 |
|---|---|---|---|---|
| 重构前 `legacy/order_service.py` | 52 | 37 | 6 | 6 |
| 重构后 `structured/order_service.py` | 61 | 46 | 6 | 6 |
| 事件声明 `structured/fields.py` | 43 | 39 | — | — |

- 埋点数量不变（6 → 6），每条旧日志都有一个对应的结构化事件。
- 业务文件行数略增是因为 `emit(...)` 调用带命名字段、可读性更高；
  字段校验、脱敏、JSON 序列化等横切逻辑全部收敛到框架中，不再散落各处。
- 事件声明集中在 `fields.py` 一个文件，一次声明、处处复用。
