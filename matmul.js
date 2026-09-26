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

module.exports = {
  createMatrix,
  blockMulAdd,
  matmulNaive,
  matmulSerialBlocked,
  matmulParallel,
  ParallelMultiplier,
};
