'use strict';
/** bench_threads.js — 线程数 vs 加速比。可用 taskset 限制核数观察受限场景。 */
const os = require('os');
const { createMatrix, matmulSerialBlocked, ParallelMultiplier } = require('./matmul');

const N = Number(process.argv[2] || 1024);
const BS = Number(process.argv[3] || 64);
const REPS = 5;

(async () => {
  let s = 1;
  const rng = () => { s ^= s << 13; s >>>= 0; s ^= s >> 17; s ^= s << 5; s >>>= 0; return s / 0xffffffff; };
  const A = createMatrix(N, N, rng), B = createMatrix(N, N, rng), C = createMatrix(N, N);

  let best = Infinity;
  for (let r = 0; r < REPS; r++) {
    const t = process.hrtime.bigint();
    matmulSerialBlocked(A, B, C, BS);
    best = Math.min(best, Number(process.hrtime.bigint() - t) / 1e6);
  }
  console.log(`机器逻辑核数: ${os.cpus().length}, 矩阵 ${N}^3, blockSize=${BS}`);
  console.log(`串行分块基线: ${best.toFixed(1)} ms\n`);
  console.log(`线程数 | 耗时 ms | 加速比 | 并行效率`);
  for (const nt of [1, 2, 4, 8, 12, 16]) {
    const pool = new ParallelMultiplier(nt);
    await pool.multiply(A, B, C, BS); // 预热
    let tp = Infinity;
    for (let r = 0; r < REPS; r++) {
      const t = process.hrtime.bigint();
      await pool.multiply(A, B, C, BS);
      tp = Math.min(tp, Number(process.hrtime.bigint() - t) / 1e6);
    }
    await pool.close();
    console.log(`${String(nt).padStart(4)}   | ${tp.toFixed(1).padStart(7)} | ${(best / tp).toFixed(2)}x  | ${(best / tp / nt * 100).toFixed(0)}%`);
  }
})();
