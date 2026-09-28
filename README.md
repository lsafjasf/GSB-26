# 统一错误处理重构（Python 3，仅标准库）

## 结构

```
legacy/            重构前：各模块自造错误类型、自由文本消息
refactored/        重构后：统一 AppError + 注册表
  errors.py        统一错误类型、错误码注册表、原因链工具
  compat.py        遗留异常 -> AppError 的兼容映射层
contracts/         错误码稳定性契约 + 重构前后故障对照表（生成物）
  error_codes.json   错误码契约表（冻结 code/分类/可重试/关键上下文）
  fault_matrix.json  对照表明细（机器可核对）
  fault_matrix.md    对照表（人读，逐行 MATCH/DRIFT + 跨层稳定性）
tests/             对拍测试 + 原因链测试 + 契约稳定性测试
examples/          原因链追溯输出样例
tools/             行数对比 / 对照表生成 / 契约检查 / 变异探针
```

## 统一错误类型

`refactored/errors.py` 中的 `AppError` 携带五个要素：

- `category`：错误分类（`Category` 枚举），由注册表推导，调用方不可伪造
- `code`：错误码，必须在 `_REGISTRY` 注册，未注册直接 `ValueError`
- `cause`：原始原因，经 `__cause__` 串成可追溯原因链
- `context`：结构化上下文（`url`、`dsn`、`user_id` 等），禁止以拼接自由文本作为唯一信息
- `retryable`：是否可重试，默认由注册表推导，允许单点覆盖

**新增一类错误的唯一改动位置**：`refactored/errors.py` 的 `_REGISTRY`
增加一个 `ErrorSpec` 条目（分类、默认可重试性、消息模板）。各模块只引用
错误码，消息文案、分类、可重试性全部集中在一处。

## 错误码稳定性契约（防语义漂移）

统一错误类型之后，注册表本身仍可能被随手删码、改分类或翻转可重试标记而
无人察觉。为此建立两层稳定性约束：

1. **契约表 `contracts/error_codes.json`** 是对外承诺的机器可读清单，
   冻结字段为 `code` / `category` / `retryable` / `required_context`：
   - 已发布错误码不得删除、改名或改义；新增字段只能追加；
   - 新增错误码必须先入契约再在 `_REGISTRY` 使用；
   - 破坏性变更必须提升契约 `version` 并走迁移评审；
   - `message_template` 仅作文档快照，不参与兼容判定（允许改文案）。
2. **兼容性检查 `tools/check_contract.py`**（退出码非 0 即失败，可接 CI）：
   - A 契约表自身合法（版本、冻结字段、分类枚举、错误码命名）；
   - B `_REGISTRY` 与契约表逐项一致——错误码集合相同（删码/改名/私增即失败），
     分类、可重试性、必填上下文字段逐一相同（改义即失败）；
   - C 对照表产物必须是当前代码 + 当前契约的新鲜生成结果，手改或漏刷新即失败；
   - D 同一错误码出现在任何模块、任何层位、legacy/refactored 任一侧，
     分类与可重试标记必须一致。

## 重构前后故障对照表

`tools/fault_matrix.py` 把同一组下游故障（网络超时、连接拒绝、存储连不上、
查询死锁、缓存宕机）分别注入新旧实现，覆盖三个层位：模块级直接抛出、
`service.load_profile` 包装后的外层、被包装的服务级内层。每行把两侧错误
投影为 `(code, category, retryable, 关键上下文)` 并给出 MATCH/DRIFT 裁定，
可逐条核对；汇总表另查“服务级内层 code 必须等于模块级 code”，防止包装层
改写下游语义。产物 `contracts/fault_matrix.md` / `.json` 由脚本生成，
当前 13 行全部 MATCH、跨层全部 STABLE。

`tools/mutation_probe.sh` 在仓库的临时副本上独立施加四类真实破坏
（删除 `STORE_CONN`、把 `CACHE_BACKEND_DOWN` 分类改成 internal、
把 `STORE_QUERY.retryable` 翻成 True、手改对照表产物），逐一证明
检查脚本以非零码退出并报出对应原因；工作区文件不被修改。

## 错误等价（对拍）

`tests/test_equivalence.py` 对每个故障场景（网络超时、连接拒绝、存储
连接失败、查询死锁、缓存后端宕机）分别注入同一故障到新旧实现，把两侧
错误投影为 `(code, category, retryable, 关键上下文)` 规范化元组逐例比较：

- 模块级对拍：`net` / `store` / `cache` 逐模块比较
- 服务级对拍：多层包装后，外层与内层（下游）错误都分别比较
- `test_no_key_info_lost`：沿原因链逐层断言关键上下文字段无丢失

## 原因链追溯

`raise AppError(...) from exc` 保留完整原因链，`format_chain` 输出样例
（`python3 examples/chain_demo.py`）：

```
PROFILE_LOAD_FAILED [internal] retryable=False: failed to load profile for user 42 (user_id='42')
  caused by: STORE_CONN [storage] retryable=True: cannot connect to store db://main (dsn='db://main')
    caused by: ConnectionRefusedError: [Errno 111] Connection refused
```

重构前 `legacy/service.py` 用 `str(exc)` 拼消息，底层 `OSError` 被彻底
丢弃；重构后三层包装仍可追溯到底层 `ConnectionRefusedError`。

## 调用方兼容性

`refactored/compat.py` 的 `from_legacy(exc)` 保留既有错误类型映射关系：

| 遗留类型 | 映射后 code | 说明 |
|---|---|---|
| `net.NetTimeout` | `NET_TIMEOUT` | 从消息解析 `url`、`timeout` |
| `net.NetConnRefused` | `NET_CONN_REFUSED` | 从消息解析 `url` |
| `store.StoreError`（`E_CONN`） | `STORE_CONN` | 错误码原本藏在消息前缀里 |
| `store.StoreError`（`E_QUERY`） | `STORE_QUERY` | 同上 |
| `cache.CacheDown` | `CACHE_BACKEND_DOWN` | 新增可重试标记（原来没有） |
| `service.ServiceError` | `PROFILE_LOAD_FAILED` | 内层下游错误按既有消息格式尽力还原 |

需要调整的调用点及原因：

1. **`except net.NetTimeout` 等按类型捕获的调用点**：改为
   `except AppError as e` 后判断 `e.code` / `e.category`；过渡期可在
   系统边界用 `compat.from_legacy` 适配，无需一次性改完。
2. **用正则/子串匹配 `StoreError` 消息中 `E_CONN` 的调用点**：改为读
   `e.code`，不再解析自由文本。
3. **依赖 `ServiceError` 消息文本（`"profile fetch failed for user ..."`）
   的调用点**：消息模板改为 `"failed to load profile for user {user_id}"`，
   文本匹配必须迁移到 `e.context["user_id"]`。
4. **依赖 `CacheDown` 判断能否重试的调用点**：原来没有可重试信息，
   现在直接读 `e.retryable`。

无需调整的调用点：只判断"是否抛异常"和只把异常交给日志的调用点，
`AppError` 仍是 `Exception`，`str(e)` 仍产出人类可读消息。

## 行数对比（`python3 tools/loc_compare.py`）

| 版本 | 有效行 | 错误处理相关行 |
|---|---|---|
| 重构前（4 个模块） | 54 | 30 |
| 重构后（errors + 4 个模块） | 120 | 36 |
| 兼容层（过渡期可删除） | 61 | 13 |

重构前错误处理代码虽少，但 4 个模块有 4 套错误约定；重构后核心错误
逻辑集中在 `errors.py` 一处，模块内每处 `raise` 仅 1–2 行。兼容层是
一次性适配代码，调用方迁移完成后可整体删除。

## 运行命令

```bash
python3 -m unittest discover -s tests -v   # 对拍 + 原因链 + 契约测试（17 例）
python3 examples/chain_demo.py             # 原因链追溯输出样例
python3 tools/loc_compare.py               # 重构前后行数对比
python3 tools/fault_matrix.py              # 重新生成故障对照表
python3 tools/fault_matrix.py --stdout     # 只打印 Markdown 对照表
python3 tools/check_contract.py            # 契约/对照表兼容性检查（CI 可直接用）
python3 tools/mutation_probe.sh            # 删改错误码的失败性验证（真实输出）
```
