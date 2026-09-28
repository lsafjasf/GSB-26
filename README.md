# GSB-26 — 多来源时间线对齐与合并库（timeline_align）

纯 Python 3 标准库实现，时间用整数表示（如毫秒/微秒）。多个设备各自记录
时间序列，设备时钟存在固定偏移与轻微线性漂移：

    t_ref ~= a * t_src + b        (a = 1 + drift, b = offset)

## 运行命令

```bash
python3 -m unittest discover -s tests -v   # 自测：合成数据验证 + 不变量断言
python3 examples/demo.py                   # 端到端演示：估计值 vs 真值、质量数据
python3 examples/joint_demo.py             # 联合对齐演示：联合 vs 逐对 vs 真值、bootstrap 验证
```

## 功能

- **偏移/漂移估计**
  - `estimate_offset_from_common_events(ref, src)`：对公共事件（共享 `key`）
    做最小二乘拟合，输出偏移、漂移及 1σ 不确定度、残差标准差。
  - `estimate_offset_xcorr(ref, src, max_lag, bin_width)`：无公共事件时，
    对事件流做稀疏互相关，输出偏移及不确定度（仅偏移，不含漂移）。
- **联合估计（≥3 个来源）**
  - `estimate_joint(sources, ref_name)`：以全部公共事件构造方程组
    `a_i * t_i + b_i = tau_k`（tau_k 为事件在参考时钟上的潜时刻，参考源
    固定为恒等映射作为规范约束），消去 tau_k 后联立求解所有来源的偏移
    与漂移；非参考源之间共享的公共事件（B-C、C-D…）也进入方程组。
    每个参数输出 1σ 不确定度（联合协方差矩阵 `sigma^2 (X^T X)^-1`），
    结果可直接传给 `merge`。
  - `bootstrap_joint(sources, ref_name, n_resamples, seed)`：按事件簇重采样
    重复求解，输出各参数的 bootstrap 标准差，用于验证解析不确定度。
  - 联合与逐对估计使用同一参考约束，二者在组合不确定度内相容；
    由 `tests/test_joint.py` 断言（含 B→C 复合变换闭合检验）。
- **合并** `merge(sources, alignments, bin_width)`：所有事件映射到参考时钟后，
  按 `floor(t_aligned / bin_width)` 归入半开区间桶并全局排序。
  **区间归属规则**：每个事件恰好落入一个桶，不丢弃、不重复；采样率不同
  的来源只是每桶事件数不同，需要均匀网格的消费者可对桶内单元自行聚合。
- **对齐质量** `quality_report(...)`：对齐后公共事件残差分布
  （mean / std / p95_abs / max_abs）。
- **缺失 vs 无事件**：每个来源声明 `coverage` 录制区间；桶在覆盖范围外为
  `missing`（设备未录制），覆盖范围内无事件为 `empty`（在录制但无事件）。

## 合并不变量

每个来源的事件在合并结果中的出现次数等于输入次数，且全局按对齐时间
排序。由 `tests/test_timeline_align.py::TestMerge` 断言保证。

## 合成数据验证

`tests/test_timeline_align.py` 构造三台设备：已知偏移（0 / 1500 / -800）
与漂移（0 / +200ppm / -150ppm）、80 个公共事件（±3 抖动）、不同采样率的
私有事件、B 的录制缺口、C 的静默期。断言估计值在容差内恢复真值；
`examples/demo.py` 打印估计值 vs 真值与残差分布等对齐质量数据。

`tests/test_joint.py` 构造四台设备（A 为参考）与非星型公共事件拓扑
（全体共享 + 仅 B-C / 仅 C-D / 仅 A-D），断言：联合估计恢复真值；
联合与逐对估计在 4σ 组合不确定度内相容；B→C 复合变换与直接逐对拟合
闭合；解析不确定度与 300 次事件簇 bootstrap 标准差比值在 [0.5, 2]；
合并后各来源采样数不变且全局按统一时间轴排序。

## 文件

- `timeline_align/core.py` — 库实现
- `timeline_align/joint.py` — 多来源联合估计 + bootstrap 验证
- `tests/test_timeline_align.py` — 逐对估计与合并的合成数据验证（14 个用例）
- `tests/test_joint.py` — 联合估计验证：真值恢复、逐对相容、闭合、bootstrap（12 个用例）
- `examples/demo.py` — 端到端演示，输出对齐质量数据
- `examples/joint_demo.py` — 联合对齐演示：联合 vs 逐对 vs 真值、不确定度验证
