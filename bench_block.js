'use strict';
/** bench_block.js — 块大小 vs 耗时曲线（串行分块 / 8 线程并行 / 朴素对照）。 */
const { createMatrix, matmulNaive, matmulSerialBlocked, ParallelMultiplier } = require('./matmul');

const N = Number(process.argv[2] || 1024);
const REPS = 3;
const BLOCK_SIZES = [8, 16, 32, 64, 128, 256];

function timeMin(fn) {
  let best = Infinity;
  for (let r = 0; r < REPS; r++) {
    const t = process.hrtime.bigint();
    fn();
    best = Math.min(best, Number(process.hrtime.bigint() - t) / 1e6);
  }
  return best;
}

(async () => {
  let s = 1;
  const rng = () => { s ^= s << 13; s >>>= 0; s ^= s >> 17; s ^= s << 5; s >>>= 0; return s / 0xffffffff; };
  const A = createMatrix(N, N, rng), B = createMatrix(N, N, rng), C = createMatrix(N, N);
  const gflops = ms => (2 * N ** 3 / (ms / 1e3) / 1e9).toFixed(2);

  const tNaive = timeMin(() => matmulNaive(A, B, C));
  console.log(`naive(ijk, 无分块)        ${tNaive.toFixed(1).padStart(9)} ms  ${gflops(tNaive)} GFLOP/s`);

  const pool = new ParallelMultiplier(8);
  await pool.multiply(A, B, C, 64); // 预热 JIT
  console.log(`\nblockSize | 串行分块 ms (GFLOP/s) | 并行8线程 ms (GFLOP/s) | 并行加速比`);
  for (const bs of BLOCK_SIZES) {
    const ts = timeMin(() => matmulSerialBlocked(A, B, C, bs));
    let tp = Infinity;
    for (let r = 0; r < REPS; r++) {
      const t = process.hrtime.bigint();
      await pool.multiply(A, B, C, bs);
      tp = Math.min(tp, Number(process.hrtime.bigint() - t) / 1e6);
    }
    console.log(`${String(bs).padStart(6)}   | ${ts.toFixed(1).padStart(8)} (${gflops(ts).padStart(5)})      | ${tp.toFixed(1).padStart(8)} (${gflops(tp).padStart(5)})      | ${(ts / tp).toFixed(2)}x`);
  }
  await pool.close();
})();
