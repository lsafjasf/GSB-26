# 错误处理重构：统一错误类型 + 对拍回归

纯 Python 3 标准库，无第三方依赖。

## 目录结构

```
legacy/    重构前：各模块自造错误类型、自由文本消息
unified/   重构后：统一错误层
  errors.py   AppError + Category + ERROR_REGISTRY（唯一事实来源）
  compat.py   legacy 异常 <-> 统一错误码 的映射（迁移期适配器）
  net_client.py / storage.py / auth.py / service.py
tests/     对拍等价测试 + 原因链测试
run_tests.sh
```

## 统一错误类型

`AppError` 携带五个要素，全部由 `ERROR_REGISTRY` 派生，业务代码不得拼接自由文本作为唯一信息：

| 要素 | 字段 | 来源 |
|---|---|---|
| 分类 | `category` (network/storage/auth/validation/internal) | 注册表按错误码唯一决定 |
| 错误码 | `code`，如 `NET_CONNECT_TIMEOUT` | 必须在注册表中，否则 `KeyError` 立即暴露 |
| 原始原因 | `cause` / `__cause__` | `raise AppError(...) from exc` |
| 上下文 | `context`（结构化 dict） | 抛出点传入，模板渲染消息 |
| 可重试 | `retryable` | 注册表默认值，抛出点可覆盖 |

**新增一类错误的唯一改动位置**：`unified/errors.py` 的 `ERROR_REGISTRY` 增加一行
（`tests/test_equivalence.py::test_registry_is_single_source_of_truth` 会校验模块中出现的
每个错误码都已注册，防止绕过）。

## 运行命令

```bash
cd error_refactor
./run_tests.sh                       # 或 python3 -m unittest discover -s tests -v
```

## 对拍测试（重构前后错误等价）

`tests/test_equivalence.py` 定义 9 个故障场景（超时/拒绝/HTTP 4xx/5xx/磁盘满/IO 错误/
令牌过期/越权/空令牌），同一批注入式故障分别驱动 legacy 与 unified 实现，逐例断言：

1. 错误码等价（legacy 异常经 `compat.map_legacy` 映射后与 `AppError.code` 一致）；
2. 分类与可重试性等价（由注册表唯一决定）；
3. 原有关键信息不丢失：legacy 能提供的每个上下文键值，unified 必须全部保留且相等；
4. 成功路径行为不变；`to_legacy` 回转后类型与消息与旧异常完全一致。

## 原因链输出样例

多层包装（服务层 → 网络层 → 底层 socket）后，`format_chain` 实际输出：

```
[0] internal/INTERNAL_UNEXPECTED retryable=True: unexpected error: fetch /metrics failed | context={'detail': 'fetch /metrics failed'}
[1] network/NET_CONNECT_TIMEOUT retryable=True: connect to db.internal:9000 timed out | context={'host': 'db.internal', 'port': 9000}
[2] root TimeoutError: timed out
```

`iter_chain` 沿 `AppError.cause`/`__cause__` 追溯到底层原因（带循环保护），
errno 等底层细节（如 `OSError.errno == ENOSPC`）在链末端仍可取到。

## 调用方兼容性

既有错误类型的映射关系集中在 `unified/compat.py`（单一事实来源）：

| legacy 异常 | 统一错误码 | 关键上下文 |
|---|---|---|
| `ConnectFailure`("...timed out") | `NET_CONNECT_TIMEOUT` | host, port |
| `ConnectFailure`("...refused") | `NET_CONNECT_REFUSED` | host, port |
| `HttpError` (status>=500) | `NET_HTTP_5XX` | status, url |
| `HttpError` (status>=400) | `NET_HTTP_4XX` | status, url |
| `DiskFullError` | `STORAGE_DISK_FULL` | path |
| 其他 `OSError`（原样外泄的） | `STORAGE_IO` | path, errno |
| `AuthException`("token expired") | `AUTH_TOKEN_EXPIRED` | — |
| `AuthException`("forbidden...") | `AUTH_FORBIDDEN` | — |
| `BadInput` | `VALIDATION_EMPTY_TOKEN` | — |

需要调整的调用点及原因：

- **`except HttpError as e: e.status` 类调用点**：改为 `except AppError as e: if e.code in ("NET_HTTP_4XX", "NET_HTTP_5XX"): e.context["status"]`。
  原因：状态码从异常属性迁入结构化 `context`，分类语义由 `code` 表达。
- **靠 `str(e)` 文本匹配区分"过期/越权"的调用点**：改为判断 `e.code`。
  原因：文本不再是契约，错误码才是；这是本次重构消除的主要隐患。
- **捕获裸 `OSError` 处理存储失败的调用点**：改为捕获 `AppError` 并按 `code` 分支。
  原因：unified 不再让底层 `OSError` 原样外泄，errno 保留在 `__cause__` 与 `context` 中。
- **暂不能改造的调用点**：在边界处用 `compat.to_legacy(app_error)` 转回旧异常类型，
  类型与 `str()` 均与旧实现一致（有对拍测试保证），可灰度迁移，迁移完成后删除 `compat.py`。

## 行数对比（错误处理相关代码）

| 侧 | 文件 | 行数 |
|---|---|---|
| legacy | `errors.py` 26 + `net_client.py` 21 + `storage.py` 16 + `auth.py` 19 | **82** |
| unified | `errors.py` 77 + `net_client.py` 20 + `storage.py` 15 + `auth.py` 18 | **130** |
| unified（迁移期临时） | `compat.py` 52 + `service.py` 14（演示层） | 66 |

说明：unified 的 77 行 `errors.py` 是一次性基础设施（注册表 + 原因链渲染），
各模块内错误处理代码从 56 行降至 53 行且不再含消息拼接；**新增一类错误的成本
从"新异常类（约 4 行）+ 各抛出点拼消息"降为注册表 +1 行**。`compat.py` 为迁移期
适配层，调用点改造完成后可整体删除。
