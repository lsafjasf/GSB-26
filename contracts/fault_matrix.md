# 重构前后下游故障对照表

- 生成命令：`python3 tools/fault_matrix.py`（生成物，请勿手改）
- 契约版本：`1`；投影字段：code / category / retryable / 关键上下文
- 同一行左（legacy 经 `compat.from_legacy` 投影）右（refactored 原生）为同一次故障注入，逐字段可核对。

| 故障场景 | 层位 | legacy 类型 | 旧 code | 旧分类 | 旧可重试 | 新类型 | 新 code | 新分类 | 新可重试 | 关键上下文（两侧一致） | 裁定 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| cache_backend_down | 模块级 | CacheDown | CACHE_BACKEND_DOWN | cache | True | AppError | CACHE_BACKEND_DOWN | cache | True | key='profile:7' | MATCH |
| cache_backend_down | 服务级·内层 | AppError | CACHE_BACKEND_DOWN | cache | True | AppError | CACHE_BACKEND_DOWN | cache | True | key='profile:42' | MATCH |
| cache_backend_down | 服务级·外层 | ServiceError | PROFILE_LOAD_FAILED | internal | False | AppError | PROFILE_LOAD_FAILED | internal | False | user_id='42' | MATCH |
| net_conn_refused | 模块级 | NetConnRefused | NET_CONN_REFUSED | network | True | AppError | NET_CONN_REFUSED | network | True | url='http://api.internal/users/7' | MATCH |
| net_conn_refused | 服务级·内层 | AppError | NET_CONN_REFUSED | network | True | AppError | NET_CONN_REFUSED | network | True | url='http://api.internal/users/42' | MATCH |
| net_conn_refused | 服务级·外层 | ServiceError | PROFILE_LOAD_FAILED | internal | False | AppError | PROFILE_LOAD_FAILED | internal | False | user_id='42' | MATCH |
| net_timeout | 模块级 | NetTimeout | NET_TIMEOUT | network | True | AppError | NET_TIMEOUT | network | True | timeout=1.5, url='http://api.internal/users/7' | MATCH |
| net_timeout | 服务级·内层 | AppError | NET_TIMEOUT | network | True | AppError | NET_TIMEOUT | network | True | timeout=3.0, url='http://api.internal/users/42' | MATCH |
| net_timeout | 服务级·外层 | ServiceError | PROFILE_LOAD_FAILED | internal | False | AppError | PROFILE_LOAD_FAILED | internal | False | user_id='42' | MATCH |
| store_conn | 模块级 | StoreError | STORE_CONN | storage | True | AppError | STORE_CONN | storage | True | dsn='db://main' | MATCH |
| store_conn | 服务级·内层 | AppError | STORE_CONN | storage | True | AppError | STORE_CONN | storage | True | dsn='db://main' | MATCH |
| store_conn | 服务级·外层 | ServiceError | PROFILE_LOAD_FAILED | internal | False | AppError | PROFILE_LOAD_FAILED | internal | False | user_id='42' | MATCH |
| store_query | 模块级 | StoreError | STORE_QUERY | storage | False | AppError | STORE_QUERY | storage | False | sql='UPDATE profile SET name=%s' | MATCH |

## 汇总

- 对照行：13；MATCH：13；DRIFT：0
- 跨层稳定性：服务级内层 code 必须与模块级 code 完全一致（包装层只允许新增外层码，禁止改写下游语义）。

| 故障场景 | 模块级 code | 服务级内层 code | 外层 code | 跨层裁定 |
|---|---|---|---|---|
| cache_backend_down | CACHE_BACKEND_DOWN | CACHE_BACKEND_DOWN | PROFILE_LOAD_FAILED | STABLE |
| net_conn_refused | NET_CONN_REFUSED | NET_CONN_REFUSED | PROFILE_LOAD_FAILED | STABLE |
| net_timeout | NET_TIMEOUT | NET_TIMEOUT | PROFILE_LOAD_FAILED | STABLE |
| store_conn | STORE_CONN | STORE_CONN | PROFILE_LOAD_FAILED | STABLE |
| store_query | STORE_QUERY | - | - | STABLE |
