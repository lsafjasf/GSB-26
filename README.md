# GSB-26：请求批处理与合并库（Python 3，仅标准库）

高频小请求在短窗口内按键合并、按批提交，每个调用方拿到属于自己的结果或错误。

## 文件

- `batcher.py` — 库源码（`Batcher`、`BatchTimeoutError`、`BatchResultError`、`ThreadingScheduler`）
- `test_batcher.py` — 自测（含可注入虚拟时钟 `ManualScheduler`）

## 运行测试

```bash
python3 -m unittest test_batcher -v
```

## 用法

```python
from batcher import Batcher

def handler(requests):          # requests: list，返回等长 list
    return [do_rpc(r) for r in requests]   # 某个元素是 Exception 实例 => 该条失败

b = Batcher(handler, max_batch_size=100, window=0.05, timeout=1.0)
future = b.submit("user:42", {"q": 1})   # 同 key 并发请求自动合并
result = future.result(timeout=5)        # 或 b.call("user:42", {...})
```

- **双触发**：数量达 `max_batch_size` 或窗口 `window` 秒到期，先到先触发；超限自动分片。
- **非阻塞提交**：批量提交（无论数量还是窗口触发）都在后台线程执行下游 `handler`，
  `submit` 立即返回 Future，调用方不被下游延迟阻塞。
- **合并**：同 key 未提交的请求共享一条下游请求与结果，`b.stats` 给出计数。
- **失败分发**：子请求失败（返回元素为异常）只影响对应调用方；handler 抛异常 => 整体失败广播；
  超时 => 所有等待方收到 `BatchTimeoutError`（`TimeoutError` 子类，可与整体失败区分），无人挂起。
- **取消**：`future.cancel()` 后若该条目无人等待且未提交，则整条不下行。
- **时间注入**：`scheduler` 参数抽象定时器；测试用 `ManualScheduler` 以虚拟时间精确复现窗口边界。

## 调度器与测试覆盖

两种调度器实现分别由不同用例覆盖：

- **`ManualScheduler`（虚拟时钟，测试专用）**：`TriggerTest`、`MergeTest`、`DispatchTest`、
  `TimeoutTest`、`BoundaryTest`、`ShardTest`、`CancelTest`、`CallCountComparisonTest`。
  虚拟时间只能由用例显式推进，窗口边界、分片、取消、合并计数等场景可精确复现。
- **`ThreadingScheduler`（生产默认，真实 `threading.Timer`）**：`RealSchedulerSmokeTest`
  冒烟用例——真实线程 + 真实定时器覆盖窗口触发、数量触发，并回归"提交方不被
  handler 阻塞"（数量触发曾在提交方线程内同步执行 handler，凑满批次的调用方会
  承担全部下游延迟；虚拟时钟下 handler 瞬时完成，该缺陷无法暴露）。

## 调用次数对比（测试实测输出）

```
逻辑请求量:            1000   (10 个 key × 100 调用方)
不合并（直连）下游调用: 1000
合并后下游调用:        1
调用次数降低:          99.9%
```
