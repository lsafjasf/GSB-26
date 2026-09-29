# 资源容量规划计算库

纯 Python 3 标准库实现。由到达率、处理时长分布与并发上限推导排队长度、
等待时间与所需资源（线程 / 队列 / 内存 / 连接），并用内置模拟器校准解析模型。

## 文件

- `capacity_model.py` — 解析容量模型（Erlang C / Allen-Cunneen 近似）
- `simulator.py` — 离散事件模拟器（泊松/均匀/突发到达，指数/定长/对数正态服务）
- `calibrate.py` — 模型 vs 模拟校准，偏差超阈值告警（含稀有事件所需样本量估算 `required_arrivals`）
- `recommend.py` — 资源配额建议（含不确定度与依据），带 CLI
- `selftest.py` — 边界用例单元测试 + 估算耗时基准

## 模型推导

记到达率 λ（req/s），平均处理时长 1/μ（s），并发线程数 c，
服务时长变化系数 cv = 标准差/均值。

**供给负载与利用率**：a = λ/μ（Erlang），ρ = a/c。稳定条件 ρ < 1。

**Erlang C（M/M/c 精确解）**：到达需排队的概率

```
Pw = (a^c/c! · c/(c−a)) / ( Σ_{k=0}^{c−1} a^k/k! + a^c/c! · c/(c−a) )
```

实现用对数 + log-sum-exp 避免 a^c/c! 溢出（`capacity_model.erlang_c`）。

**平均等待与队长**：Wq = Pw / (cμ − λ)，再由 Little 定律 Lq = λ·Wq。

**超时概率**：M/M/c 中等待时间尾部分布为指数，
P(Wq > t) = Pw · exp(−(cμ − λ)·t)。

**长尾服务（M/G/c，Allen-Cunneen 近似）**：Wq(M/G/c) ≈ Wq(M/M/c) · (1+cv²)/2，
超时概率同乘该修正因子。cv ≤ 2 时误差通常 <10%（均值口径）。

**突发流量**：以峰值到达率 λ·peak_factor 代入稳态模型；周期性突发
（burst）无稳态解析解，直接用模拟器。

**超容量（ρ ≥ 1）**：队列发散，稳态量无意义；模型退化为流体近似，
长期超时比例 ≈ 1 − 1/ρ，并标记 `stable=False`。

### 适用条件与失效边界

- 适用：到达近似泊松、服务时长相互独立、ρ < 1、FIFO 无限队列。
- ρ > 0.9：等待对参数误差极度敏感（Wq ∝ 1/(cμ−λ)），模型给出告警。
- cv > 2（重尾）：Allen-Cunneen 误差大，模型告警，以模拟为准。
- 非泊松到达（突发、批量）：稳态模型失效，校准会触发告警，以模拟为准。
- ρ ≥ 1：只有流体近似，任何"精确"数字都不可信。

## 模拟校准数据

`python3 calibrate.py --n 100000`（阈值 15%，模拟以 abandon=False 与模型同口径）：

```
场景                         指标           模型         模拟     偏差
M/M/c 中等负载 rho=0.8       mean_wait    0.2046     0.2036    0.5%
                             mean_queue    1.637      1.626    0.7%
                             p_timeout   0.00749    0.00758    1.1%
M/M/c 高负载 rho=0.95        mean_wait     1.651      1.906   13.4%
                             mean_queue    15.69      18.07   13.2%
                             p_timeout    0.3037     0.3251    6.6%
M/G/c 长尾 cv=2              mean_wait    0.5115     0.3518   45.4%  [告警]
                             p_timeout   0.01874    0.04701   60.1%  [告警]
突发流量 (每10s突发80个)      mean_queue    1.637      25.49   93.6%  [告警]
                             p_timeout   0.00749     0.6368   98.8%  [告警]
```

结论：M/M/c 假设成立时模型与模拟吻合（≤15%）；长尾 cv=2 与突发到达
触发告警，量化展示了解析模型的失效边界。

注意：表中 p_timeout 为单次模拟值（n=10⁵, seed=42），稀有事件估计
噪声大，不能直接当作"精确"数字引用，见下节。

### 样本量与估计偏差（稀有事件）

超时概率 p 是稀有事件，二项估计 p̂ = k/n 的标准误为 √(p(1−p)/n)，
相对标准误 √((1−p)/(n·p))。要达到相对误差 ε（置信水平由 z 给定），
所需样本量 n ≥ z²(1−p)/(ε²·p)，随 1/p 增长
（`calibrate.required_arrivals`）：

```
p = 7.5e-3 (rho=0.8 场景), ε=15%, z=1.96  →  n ≥ 约 2.3 万
p = 1e-4,                  ε=15%, z=1.96  →  n ≥ 约 171 万
```

更关键的是相关性：超时事件在同一拥堵期成批出现、彼此相关，
有效样本量远小于到达数。实测均值标准误为二项 SE 的 4~9 倍
（rho=0.8 → 4.2x，rho=0.95 → 9.1x），即上式只是下限。

实测（M/M/c rho=0.8，模型精确值 0.00749，不同种子）：

```
n=1e5:  0.00508 / 0.00654 / 0.01213   → 偏差 −32% ~ +62%
n=1e6:  0.00786 / 0.00744 / 0.00837   → 偏差 ±12% 以内
rho=0.95 场景 n=1e6 三次均值 0.30367，与模型 0.30372 吻合 (0.01%)
```

自测做法：`selftest.test_calibration_p_timeout` 以 8 次独立重复 ×
20 万到达估计均值与 SEM，断言单次样本量满足所需样本量公式、
相对 SEM ≤ 15%、且模型值落在 mean ± 3·SEM 内。

## 配额建议样例

`python3 recommend.py --rate 120 --service-ms 50 --cv 1.5 --timeout 0.5 --target 0.001 --peak-factor 2 --burst 200 --calib-dev 0.15`

```
线程数:   15  (敏感区间 13 ~ 22)
队列长度: 200
连接数:   215
内存:     29.3 MB
预测: rho=0.80 Wq=8.6ms P(超时)=4.85e-14
依据:   峰值 240 req/s 下满足 rho<=0.8 且 P(等待>0.5s)<=0.001；
        队列 = max(超时内净排空 30 位, 突发 200)；内存 = 线程+队列+连接线性加总
不确定度: 到达率/处理时长 ±20% 重估得线程区间 [13, 22]；
        校准偏差 15% 建议同比放大配额；cv=1.5 近似可信
```

## 边界情形

`selftest.py` 覆盖：零流量（指标全 0）、单请求（无等待）、
远超容量 ρ=10（stable=False，流体近似超时比例 0.9，模拟利用率≈1）、
非法参数（负到达率、零处理时长、零线程、负 cv/timeout 均抛 ValueError）。

## 估算耗时（实测，Python 3.12）

```
解析模型 capacity_metrics:  c=1 → 1.7us   c=100 → 15us   c=5000 → 0.68ms
模拟器 simulate:            n=1e4 → 4ms    n=1e5 → 30~50ms   n=1e6 → 0.39s
配额建议 recommend:         ≈0.1ms
```

## 运行命令

```bash
python3 selftest.py                     # 单元测试 + 耗时基准
python3 calibrate.py --n 100000         # 模型 vs 模拟校准
python3 recommend.py --rate 120 --service-ms 50 --cv 1.5 \
    --timeout 0.5 --target 0.001 --peak-factor 2 --burst 200
```

库用法：

```python
from capacity_model import capacity_metrics
from simulator import simulate
from recommend import recommend

m = capacity_metrics(lam=100, service_mean=0.05, c=8, cv=1.5, timeout=0.5)
s = simulate(100, 0.05, 8, cv=1.5, timeout=0.5, n_arrivals=50000)
q = recommend(100, 0.05, cv=1.5, timeout=0.5, peak_factor=2.0, burst_size=200)
```
