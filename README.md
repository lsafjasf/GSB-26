# GSB-26

多线程聚合统计缺陷修复（Python 3，仅标准库），代码在 `aggregator/`。

## 文件

- `aggregator/aggregator_buggy.py` — 缺陷版（仅用于复现，勿用于生产）
- `aggregator/aggregator.py` — 修复版（单锁 + batch_id 幂等去重 + 整批预校验）
- `aggregator/test_reproduce.py` — 缺陷对照：test_1 按严格口径断言并发 exactly-once
  （事件钉死竞态窗口，在缺陷版上确定性失败并暴露缺陷幅度）；test_2~4 复现缺陷行为（在缺陷版上通过）
- `aggregator/test_aggregator.py` — 修复版回归：自洽 / 幂等 / 单调 / 并发一致读 / all-or-nothing
- `aggregator/bench.py` — 修复前后多线程吞吐对比

## 运行

```bash
cd aggregator
python3 -m unittest test_reproduce -v   # test_1 在缺陷版上确定性失败（预期），test_2~4 复现缺陷
python3 -m unittest test_aggregator -v  # 修复版回归断言
python3 bench.py                        # 吞吐对比
```
