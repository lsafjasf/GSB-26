# 分块并行矩阵乘法（Node.js，仅标准库）

分块（tiling）+ 多线程的稠密矩阵乘法库，使用 `worker_threads` 与 `SharedArrayBuffer`，无任何第三方依赖。要求 Node.js >= 18。

## 设计

- **数据布局**：行主序 `Float64Array`，底层为 `SharedArrayBuffer`（`src/matrix.js`）。`postMessage` 对 SAB 按引用共享，worker 拿到的不是输入副本。
- **分块内核**：`src/kernel.js` 的 `multiplyTile` 按 `i-k-j` 顺序计算一个输出瓦片，`blockSize` 同时约束 i/j/k 三个维度；行主序下内层 `j` 循环对 B、C 都是连续访问，对缓存友好。
- **并行调度**：`src/parallel-matmul.js` 的 `MatMulPool` 维护可复用 worker 池。输出被划分为 `ceil(M/B) * ceil(N/B)` 个瓦片，worker `w` 处理编号 `tile % workerCount === w` 的瓦片；只向每个 worker 发送常量大小（约 256 字节）的分片描述符，不发送瓦片数组，更不复制矩阵。
- **存活检测与自愈**：池为每个 worker 监听 `exit`；worker 意外终止后立即从 `workers`/`idleWorkers` 中剔除，下次提交任务时按需要补齐，即使全部 worker 死亡也能从零重建，无需重启进程。执行中途有 worker 死亡时当前这一次乘法会被拒绝（输出缓冲可能只写了一半），但池仍可继续使用；任务带单调递增的 `jobId`，迟到的过期 `done`/`error` 消息会被丢弃，不会污染下一次乘法。
- **确定性**：每个输出元素 `C[i][j]` 只被一个瓦片写入，且该元素内部严格按 `k` 升序累加，与线程数、瓦片完成顺序无关，因此并行结果与分块串行结果**逐位相同**。
- **退化路径**：`workers = 1` 或可用瓦片数不足时直接在主线程跑同一串行内核，不付线程/消息开销；`M=0`、`K=0`、`N=0` 返回正确形状的空结果或零矩阵。

## 运行

```bash
npm test                 # 串并行一致性 + 形状/参数边界自测
npm run benchmark        # 块大小曲线 + 线程扩展曲线，写入 benchmark/results/
```

基准可用环境变量调整：`BENCH_N`（默认 384）、`BENCH_REPS`（默认 3，取中位数）、`BENCH_MAX_WORKERS`（默认 min(可用核数, 8)）、`BENCH_INCLUDE_SMT=1`（追加全部逻辑核）。受限核数复现：

```bash
taskset -c 0,2,4,6,8,10,12,14 env BENCH_N=640 BENCH_REPS=5 BENCH_MAX_WORKERS=8 node benchmark/benchmark.js
```

产物：`benchmark/results/` 下的 `block-size.csv`、`speedup.csv`、`block-size.svg`、`speedup.svg`、`BENCHMARK.md`、`benchmark-results.json`。

## API

```js
const { Matrix } = require('./src/matrix');
const { MatMulPool, multiplyParallel, blockedSerialMultiply } = require('./src/parallel-matmul');

const a = new Matrix(1000, 513);   // SharedArrayBuffer 后端
const b = new Matrix(513, 777);

const pool = new MatMulPool(8);                       // 可复用线程池
const c = await pool.multiply(a, b, { blockSize: 64, workers: 8 });
await pool.destroy();

const c2 = await multiplyParallel(a, b, { blockSize: 64 });  // 一次性便捷接口
const cs = blockedSerialMultiply(a, b, 64);                  // 分块串行基线
```

## 串并行一致性与浮点误差

- 自测对 11 种形状 × 7 种块大小 × 4 种线程数（共 308 组）断言并行输出与分块串行输出**逐位相等**（`Object.is`），并断言输出缓冲区独立于输入。
- 误差来源：浮点加法不满足结合律，只有改变累加顺序才会产生误差。本库固定了每个元素的累加顺序，所以并行本身不引入误差；若与不同累加顺序的参考实现比较，则会出现 `O(K * eps)` 量级差异（`eps = 2^-52`）。
- 容差选择（`test/self-test.js` 的 `compareTolerance`）：`|x - y| <= atol + rtol * max(|x|, |y|)`，其中 `atol = 16 * eps * max(1, K) * (1 + max|A|, max|B|)`、`rtol = 16 * eps * K`。系数 16 是对 `K` 次累加最坏舍入的保守放大。实测与反向累加参考的最大绝对差为 `4.263e-14`（K=53，值域 ±3），远在容差内。

## 形状覆盖

`test/self-test.js` 覆盖：单元素 `1x1x1`；极扁 `1x301x5`、`3x333x2`；极长 `301x1x37`、`333x2x7`；非整块尺寸 `37x31x29`、`65x33x129`、`17x64x19`；零矩阵；零维 `K=0`、`M=0`、`N=0`；以及非法形状、非法块大小/线程数、池并发调用的报错路径。

## 实测数据

环境：Node v18.19.1，AMD Ryzen 7 9700X（8 物理核 / 16 逻辑核），进程用 `taskset` 绑定 8 个物理核；640x640 双精度，每点 5 次取中位数。完整数据见 `benchmark/results/BENCHMARK.md`。

块大小（8 workers 并行 / 分块串行，单位 ms）：

| Block | 4 | 8 | 16 | 24 | 32 | 48 | 64 | 96 | 128 | 192 | 256 | 640 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 串行 | 272.4 | 205.2 | 196.0 | 183.4 | 165.0 | 166.5 | 159.9 | 159.7 | 154.8 | 166.7 | 164.1 | 156.0 |
| 并行 | 76.5 | 63.7 | 63.1 | 48.2 | 45.2 | 46.5 | **37.2** | 38.5 | 38.9 | 47.5 | 58.7 | 156.2 |

取舍依据：块太小则块循环开销大、对缓存行利用不足（4~16 明显变慢）；块太大则工作集超出 L1/L2、且瓦片数 `ceil(M/B)*ceil(N/B)` 减少——B=640 时只有 1 个瓦片，8 个 worker 中 7 个空闲，并行退化为串行。最优点在 64 附近（约 14.1 GFLOPS），默认 `blockSize = 32` 是各种形状下的稳妥值，建议在目标机器上用基准脚本调一次。

线程扩展（block = 64，基线为同一块大小的分块串行 159.9 ms）：

| Workers | 1 | 2 | 4 | 8 |
|---:|---:|---:|---:|---:|
| 耗时 ms | 158.8 | 81.3 | 44.2 | 40.3 |
| 加速比 | 1.01x | 1.97x | 3.62x | 3.97x |
| 效率 | 101% | 98% | 91% | 50% |

对照实验（不绑核、16 逻辑核，`BENCH_INCLUDE_SMT=1`）：8 线程 3.64x，16 线程 4.24x——SMT 第二个硬件线程只带来约 16% 额外吞吐，远非 2 倍。

达不到线性加速的原因：

- **内存带宽与共享缓存**：每个 worker 都要流式读取 B 的行，8 核同时打满内存子系统后带宽成为瓶颈（4 -> 8 核效率从 91% 掉到 50%）。
- **固定开销**：worker 调度、`postMessage` 同步与结果汇合是常量成本，问题规模越小占比越高（384 规模下 8 线程只有约 3.8x）。
- **SMT 不等于物理核**：超线程共享执行单元，16 逻辑线程只比 8 物理线程快约 16%。
- **负载均衡**：瓦片静态分片，瓦片数不是线程数整数倍时存在尾部空转；极扁/极长形状可用瓦片少，并行度天然受限。

## 内存上界

对 `MxK * KxN`：浮点数据上界为 `8 * (M*K + K*N + M*N)` 字节——调用方持有的 A、B 各一份，输出 C 恰好一份，**没有任何按分块复制的输入**。每个 worker 只收到常量大小（约 256 B）的分片描述符，因此并行元数据为 `O(workers)`，与矩阵规模无关；总上界 = 矩阵字节数 + `O(workers)` + Node 运行时/线程栈的固定开销。640 规模实测配置下矩阵存储为 9.38 MiB。

## 文件清单

- `src/matrix.js`：共享内存矩阵类型与形状校验
- `src/kernel.js`：分块 i-k-j 内核、串行基线、naive/反向参考实现
- `src/parallel-matmul.js`：可复用 worker 池与公开 API
- `src/matmul-worker.js`：worker 入口
- `test/self-test.js`：一致性与边界自测
- `benchmark/benchmark.js`：基准与图表/报告生成
