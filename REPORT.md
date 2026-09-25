# KVStore 接口契约报告

契约用例总数：14；被测实现：good(参考实现), bad(问题替身), minor(近似实现)。

## 1. 契约覆盖清单

| 契约点 | 用例数 | good(参考实现) | bad(问题替身) | minor(近似实现) |
|---|---:|---|---|---|
| core.read-write | 1 | PASS | PASS | PASS |
| core.overwrite | 1 | PASS | PASS | PASS |
| core.batch | 1 | PASS | PASS | PASS |
| core.size | 1 | PASS | PASS | PASS |
| error.missing-key | 1 | PASS | FAIL | PASS |
| error.invalid-key | 3 | PASS | FAIL | PASS |
| error.invalid-value | 1 | PASS | PASS | PASS |
| boundary.key-format | 1 | PASS | FAIL | FAIL |
| idempotency.repeat-call | 2 | PASS | FAIL | PASS |
| atomicity.partial-failure | 1 | PASS | FAIL | PASS |
| performance.timeout | 1 | PASS | FAIL | PASS |
| concurrency.thread-safety | 0 | 未覆盖 | 未覆盖 | 未覆盖 |

说明：PASS = 该实现满足该契约点的全部用例；FAIL = 至少一条不一致；“未覆盖” = 契约要求该点但无用例声明。

## 2. 差异报告（不一致明细）

### 实现：bad(问题替身)

| 用例 | 契约点 | 严重程度 | 差异 |
|---|---|---|---|
| error.get-missing | error.missing-key | critical | get('no-such-key'): 期望抛出 KeyNotFoundError，实际返回 None |
| error.set-empty-key | error.invalid-key | major | set('', 1): 期望抛出 InvalidKeyError，实际返回 None |
| error.set-nonstring-key | error.invalid-key | major | set(123, 1): 期望抛出 InvalidKeyError，实际抛出 TypeError |
| boundary.whitespace-key | boundary.key-format | minor | set('   ', 1): 期望抛出 InvalidKeyError，实际返回 None |
| idempotency.delete-twice | idempotency.repeat-call | major | 副作用校验 delete('k'): 期望返回 False，实际返回 True |
| atomicity.set-many-partial-failure | atomicity.partial-failure | critical | 副作用校验 size(): 期望返回 0，实际返回 1<br>副作用校验 get('ok1'): 期望抛出 KeyNotFoundError，实际返回 1 |
| performance.size-timeout | performance.timeout | major | size(): 期望返回 0，实际超时（超过 100ms 未返回） |

### 实现：minor(近似实现)

| 用例 | 契约点 | 严重程度 | 差异 |
|---|---|---|---|
| boundary.whitespace-key | boundary.key-format | minor | set('   ', 1): 期望抛出 InvalidKeyError，实际返回 None |

## 3. 兼容性判定

| 实现 | 结论 | 依据 |
|---|---|---|
| good(参考实现) | **可替换** | 全部契约用例通过，行为一致。 |
| bad(问题替身) | **不可替换** | 存在核心行为不一致（criticalx2, majorx4, minorx1），直接替换会改变调用方可观察行为。 |
| minor(近似实现) | **有条件替换** | 仅存在 1 个轻微(minor)不一致：boundary.whitespace-key；不触及核心语义，调用方若不依赖这些边界行为即可替换。 |

判定规则：全部通过为“可替换”；失败最高严重程度仅为 minor 为“有条件替换”；存在 major/critical 失败为“不可替换”。
