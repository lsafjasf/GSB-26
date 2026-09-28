'use strict';
/**
 * matmul.js — 分块并行矩阵乘法库（仅 Node.js 标准库）。
 *
 * 矩阵以行主序 Float64Array 存储，底层是 SharedArrayBuffer，
 * 因此所有 worker 线程共享同一份内存，不做任何逐块拷贝。
 *
 * 内存上界：8*(m*k + k*n + m*n) 字节（A、B、C 三份共享缓冲）
 *         + O(线程数) 的 worker 常驻开销 + O(瓦片数) 的任务表。
 *         计算过程本身不分配与矩阵规模相关的临时内存。
 */

const { Worker } = require('worker_threads');
const fs = require('fs');
const os = require('os');
const path = require('path');

/** 创建共享内存矩阵。fill 为 (i, j) => number 或省略（零矩阵）。 */
function createMatrix(rows, cols, fill) {
  if (!Number.isInteger(rows) || !Number.isInteger(cols) || rows < 0 || cols < 0) {
    throw new RangeError(`非法矩阵维度: ${rows}x${cols}`);
  }
  const buffer = new SharedArrayBuffer(rows * cols * Float64Array.BYTES_PER_ELEMENT);
  const data = new Float64Array(buffer);
  if (fill) {
    for (let i = 0; i < rows; i++) {
      for (let j = 0; j < cols; j++) data[i * cols + j] = fill(i, j);
    }
  }
  return { rows, cols, buffer, data };
}

function checkShape(A, B, C) {
  if (A.cols !== B.rows) {
    throw new RangeError(`维度不匹配: A 是 ${A.rows}x${A.cols}, B 是 ${B.rows}x${B.cols}`);
  }
  if (C && (C.rows !== A.rows || C.cols !== B.cols)) {
    throw new RangeError(`输出维度错误: 需要 ${A.rows}x${B.cols}`);
  }
}

/**
 * 核心内层计算：把 A[i0:i1, k0:k1] * B[k0:k1, j0:j1] 累加进 C[i0:i1, j0:j1]。
 * 采用 i-k-j 循环序：B 的行与 C 的行都是连续访问，对缓存友好；
 * a[i][k] 提升为标量寄存器复用。串行与并行共用此函数，
 * 保证两种路径的浮点累加顺序完全一致（结果逐位相同）。
 */
function blockMulAdd(aData, aCols, bData, bCols, cData, cCols, i0, i1, j0, j1, k0, k1) {
  for (let i = i0; i < i1; i++) {
    const aRow = i * aCols;
    const cRow = i * cCols;
    for (let kk = k0; kk < k1; kk++) {
      const aik = aData[aRow + kk];
      if (aik === 0) continue; // 稀疏/零矩阵快路径，跳过整行内层循环
      const bRow = kk * bCols;
      for (let j = j0; j < j1; j++) {
        cData[cRow + j] += aik * bData[bRow + j];
      }
    }
  }
}

/** 朴素三重循环（i-j-k），作为正确性参照与缓存不友好的对照组。 */
function matmulNaive(A, B, C) {
  checkShape(A, B, C);
  const m = A.rows, n = B.cols, k = A.cols;
  const a = A.data, b = B.data, c = C.data;
  c.fill(0);
  for (let i = 0; i < m; i++) {
    for (let j = 0; j < n; j++) {
      let sum = 0;
      for (let p = 0; p < k; p++) sum += a[i * k + p] * b[p * n + j];
      c[i * n + j] = sum;
    }
  }
  return C;
}

/** 串行分块乘法。blockSize 同时作用于 i/j/k 三个维度。 */
function matmulSerialBlocked(A, B, C, blockSize = 64) {
  checkShape(A, B, C);
  const m = A.rows, n = B.cols, k = A.cols;
  C.data.fill(0);
  for (let ii = 0; ii < m; ii += blockSize) {
    const i1 = Math.min(ii + blockSize, m);
    for (let jj = 0; jj < n; jj += blockSize) {
      const j1 = Math.min(jj + blockSize, n);
      for (let kk = 0; kk < k; kk += blockSize) {
        blockMulAdd(A.data, A.cols, B.data, B.cols, C.data, C.cols,
          ii, i1, jj, j1, kk, Math.min(kk + blockSize, k));
      }
    }
  }
  return C;
}

/** 把 C 划分为 blockSize 对齐的瓦片（边界瓦片自动截断，覆盖非整块尺寸）。 */
function buildTiles(m, n, blockSize) {
  const tiles = [];
  for (let ii = 0; ii < m; ii += blockSize) {
    for (let jj = 0; jj < n; jj += blockSize) {
      tiles.push([ii, Math.min(ii + blockSize, m), jj, Math.min(jj + blockSize, n)]);
    }
  }
  return tiles;
}

/**
 * 并行乘法线程池。worker 通过 Atomics 从共享计数器动态领取瓦片，
 * 每个 C 瓦片只被一个 worker 拥有并按固定 kk 顺序累加，
 * 因此并行结果与 matmulSerialBlocked 逐位一致（不仅仅是容差内相等）。
 */
class ParallelMultiplier {
  constructor(numThreads = require('os').cpus().length) {
    if (!Number.isInteger(numThreads) || numThreads < 1) {
      throw new RangeError(`非法线程数: ${numThreads}`);
    }
    this.numThreads = numThreads;
    this.workers = [];
    for (let t = 0; t < numThreads; t++) {
      this.workers.push(new Worker(path.join(__dirname, 'worker.js')));
    }
  }

  /** C = A * B（C 会被清零）。返回 Promise。 */
  multiply(A, B, C, blockSize = 64) {
    checkShape(A, B, C);
    C.data.fill(0);
    const tiles = buildTiles(A.rows, B.cols, blockSize);
    if (tiles.length === 0) return Promise.resolve(C); // 0 行或 0 列的退化形状
    // 共享任务计数器：worker 用 Atomics.add 动态领取下一个瓦片
    const counterBuf = new SharedArrayBuffer(4);
    const counter = new Int32Array(counterBuf);
    const task = {
      aBuf: A.buffer, bBuf: B.buffer, cBuf: C.buffer,
      m: A.rows, k: A.cols, n: B.cols,
      blockSize, tiles, counterBuf,
    };
    return Promise.all(this.workers.map(w => new Promise((resolve, reject) => {
      const onMessage = msg => {
        if (msg === 'done') { w.off('error', onError); resolve(); }
      };
      const onError = err => { w.off('message', onMessage); reject(err); };
      w.once('message', onMessage);
      w.once('error', onError);
      w.postMessage(task);
    }))).then(() => C);
  }

  close() {
    return Promise.all(this.workers.map(w => w.terminate()));
  }
}

/** 一次性便捷接口：临时建池、计算、销毁。重复调用请复用 ParallelMultiplier。 */
async function matmulParallel(A, B, C, blockSize = 64, numThreads) {
  const pool = new ParallelMultiplier(numThreads);
  try {
    return await pool.multiply(A, B, C, blockSize);
  } finally {
    await pool.close();
  }
}

/** 解析 /sys 的缓存容量字符串（"32K" / "1M" / 纯数字字节）。 */
function parseCacheSize(s) {
  const m = /^(\d+)\s*([KMG])?$/i.exec(s.trim());
  if (!m) return 0;
  const unit = { K: 1 << 10, M: 1 << 20, G: 1 << 30 }[(m[2] || '').toUpperCase()] || 1;
  return Number(m[1]) * unit;
}

let machineCache = null;
/**
 * 探测机器并行相关参数：逻辑核数、物理核数、L1d/L2/L3 容量（字节）。
 * Linux 下读 /sys/devices/system/cpu（cpu0 代表每核私有缓存）；
 * 其他平台回退到保守默认值。结果缓存，重复调用零开销。
 */
function machineInfo() {
  if (machineCache) return machineCache;
  const logicalCores = os.cpus().length;
  let physicalCores = 0;
  const cache = { L1d: 32 << 10, L2: 1 << 20, L3: 32 << 20 }; // 回退默认：典型桌面级
  try {
    const cpuDir = '/sys/devices/system/cpu';
    const cores = new Set();
    for (const name of fs.readdirSync(cpuDir)) {
      if (!/^cpu\d+$/.test(name)) continue;
      const topo = path.join(cpuDir, name, 'topology');
      const pkg = fs.readFileSync(path.join(topo, 'physical_package_id'), 'utf8').trim();
      const core = fs.readFileSync(path.join(topo, 'core_id'), 'utf8').trim();
      cores.add(`${pkg}:${core}`);
    }
    physicalCores = cores.size;
  } catch { /* 非 Linux 或权限不足：保持回退值 */ }
  if (!physicalCores) physicalCores = logicalCores;
  try {
    const cacheDir = '/sys/devices/system/cpu/cpu0/cache';
    for (const idx of fs.readdirSync(cacheDir)) {
      const dir = path.join(cacheDir, idx);
      const level = Number(fs.readFileSync(path.join(dir, 'level'), 'utf8'));
      const type = fs.readFileSync(path.join(dir, 'type'), 'utf8').trim();
      const size = parseCacheSize(fs.readFileSync(path.join(dir, 'size'), 'utf8'));
      if (!size) continue;
      if (level === 1 && type === 'Data') cache.L1d = size;
      else if (level === 2 && type === 'Unified') cache.L2 = size;
      else if (level === 3 && type === 'Unified') cache.L3 = size;
    }
  } catch { /* 保持回退值 */ }
  machineCache = { logicalCores, physicalCores, cache };
  return machineCache;
}

const MIN_BLOCK = 16;   // 更小的块循环开销占比过高（见 bench_block 实测）
const MAX_BLOCK = 256;  // 更大的块工作集超出 L2，命中率下降
const MIN_PARALLEL_FLOPS = 1e7;   // 低于此工作量，worker 调度开销大于并行收益
const FLOPS_PER_THREAD = 1e7;     // 每线程至少分摊这么多 flop 才值得多开一线程

function prevPow2(x) { return 2 ** Math.floor(Math.log2(Math.max(x, 1))); }

/**
 * 按矩阵形状 (m,k,n)、机器核数与缓存容量推荐 { blockSize, numThreads }。
 * 规则全部显式写在 reasons 里，给定相同机器与形状结果可复算：
 *  1) 块大小：A/B/C 三块 bs² 工作集(3·bs²·8B) 不超过 L2 的 1/4，
 *     取不超过该上界的最大 2 的幂，并夹在 [16, 256]；
 *  2) 线程数：物理核数（SMT 逻辑核实测收益甚微）为上限，
 *     再按工作量 flops=2mkn 收缩（<1e7 单线程；每 1e7 flop 增配一线程）；
 *  3) 负载均衡：C 瓦片数不足 2·numThreads 时减半块（下限 16），
 *     最后线程数再以瓦片数为上限（瓦片是调度最小单位）。
 * overrides 可传 { blockSize, numThreads } 手工覆盖，覆盖项跳过对应推导。
 */
function recommendConfig(m, k, n, overrides = {}) {
  const hw = machineInfo();
  const reasons = [];
  const fmtMB = b => (b >= (1 << 20) ? `${b >> 20}MB` : `${b >> 10}KB`);

  let bs;
  if (overrides.blockSize !== undefined) {
    bs = overrides.blockSize;
    reasons.push(`blockSize=${bs}：手工覆盖`);
  } else {
    const cap = Math.floor(Math.sqrt(hw.cache.L2 / (3 * 8 * 4)));
    bs = Math.min(MAX_BLOCK, Math.max(MIN_BLOCK, prevPow2(cap)));
    reasons.push(`L2=${fmtMB(hw.cache.L2)}，3·bs²·8B ≤ L2/4 → bs≤${cap}，取 2 的幂 bs=${bs}`);
  }

  const flops = 2 * m * k * n;
  let nt;
  if (overrides.numThreads !== undefined) {
    nt = overrides.numThreads;
    reasons.push(`numThreads=${nt}：手工覆盖`);
  } else {
    nt = hw.physicalCores;
    reasons.push(`物理核=${hw.physicalCores}（逻辑核=${hw.logicalCores}，SMT 边际收益低，不超额订阅）`);
    if (flops < MIN_PARALLEL_FLOPS) {
      nt = 1;
      reasons.push(`flops=${flops.toExponential(1)} < ${MIN_PARALLEL_FLOPS.toExponential(0)}，并行开销大于收益 → 单线程`);
    } else {
      const byWork = Math.max(1, Math.ceil(flops / FLOPS_PER_THREAD));
      if (byWork < nt) reasons.push(`flops=${flops.toExponential(1)}，每线程 ≥${FLOPS_PER_THREAD.toExponential(0)} flop → ${byWork} 线程`);
      nt = Math.min(nt, byWork);
    }
  }

  let tiles = Math.ceil(m / bs) * Math.ceil(n / bs);
  while (nt > 1 && bs > MIN_BLOCK && tiles < 2 * nt) {
    bs /= 2;
    tiles = Math.ceil(m / bs) * Math.ceil(n / bs);
    reasons.push(`瓦片数不足 ${2 * nt}（负载均衡需要 ≥2×线程数），块减半 → bs=${bs}（瓦片 ${tiles}）`);
  }
  if (nt > tiles) {
    reasons.push(`瓦片仅 ${tiles} 个（C 划分是并行最小单位），线程数 ${nt}→${tiles}`);
    nt = tiles;
  }
  if (nt < 1) nt = 1;

  return { blockSize: bs, numThreads: nt, tiles, machine: hw, reasons };
}

// 自动模式线程池缓存：worker 常驻，避免每次调用重复付线程创建开销
const autoPools = new Map();

/**
 * 自动配置乘法：按 recommendConfig 推导块大小与线程数后计算 C = A·B。
 * overrides = { blockSize, numThreads } 可单独或同时手工覆盖。
 * 与 matmulSerialBlocked 使用同一 kernel 与瓦片划分，结果逐位一致。
 */
async function matmulAuto(A, B, C, overrides = {}) {
  checkShape(A, B, C);
  const cfg = recommendConfig(A.rows, A.cols, B.cols, overrides);
  if (cfg.numThreads <= 1) return matmulSerialBlocked(A, B, C, cfg.blockSize);
  let pool = autoPools.get(cfg.numThreads);
  if (!pool) {
    pool = new ParallelMultiplier(cfg.numThreads);
    autoPools.set(cfg.numThreads, pool);
  }
  return pool.multiply(A, B, C, cfg.blockSize);
}

/** 关闭 matmulAuto 缓存的所有线程池（进程退出前调用，或进程直接 exit）。 */
async function closeAutoPools() {
  const pools = [...autoPools.values()];
  autoPools.clear();
  await Promise.all(pools.map(p => p.close()));
}

module.exports = {
  createMatrix,
  blockMulAdd,
  matmulNaive,
  matmulSerialBlocked,
  matmulParallel,
  ParallelMultiplier,
  machineInfo,
  recommendConfig,
  matmulAuto,
  closeAutoPools,
};
