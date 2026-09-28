'use strict';
/**
 * bench_auto.js — 自动配置 vs 手工配置在多形状下的耗时对比。
 *
 * 每个形状输出：
 *  - recommendConfig 的推荐值与逐条推导依据（可复算）；
 *  - 自动配置、手工默认(bs=64,t=8)、手工激进(bs=128,t=逻辑核) 的耗时；
 *  - 自动结果与串行分块（同 bs）的逐位一致性校验。
 * 用法: node bench_auto.js [reps缩放]
 */
const {
  createMatrix, matmulSerialBlocked, ParallelMultiplier,
  machineInfo, recommendConfig, matmulAuto, closeAutoPools,
} = require('./matmul');

const SHAPES = [
  { name: '极小 3x3x3', m: 3, k: 3, n: 3 },
  { name: '极小 32x32x32', m: 32, k: 32, n: 32 },
  { name: '极扁 4x8192x4', m: 4, k: 8192, n: 4 },
  { name: '极扁 2048x4x2048', m: 2048, k: 4, n: 2048 },
  { name: '极扁 4096x2x4096', m: 4096, k: 2, n: 4096 },
  { name: '非方阵 1000x777x999', m: 1000, k: 777, n: 999 },
  { name: '非方阵 1536x256x768', m: 1536, k: 256, n: 768 },
  { name: '方阵 512^3', m: 512, k: 512, n: 512 },
  { name: '方阵 1024^3', m: 1024, k: 1024, n: 1024 },
];

function makeRng(seed) {
  let s = seed >>> 0 || 1;
  return () => {
    s ^= s << 13; s >>>= 0; s ^= s >> 17; s ^= s << 5; s >>>= 0;
    return s / 0xffffffff;
  };
}

function maxAbsDiff(X, Y) {
  let d = 0;
  for (let i = 0; i < X.data.length; i++) {
    const v = Math.abs(X.data[i] - Y.data[i]);
    if (v > d) d = v;
  }
  return d;
}

const pools = new Map();
function getPool(nt) {
  if (!pools.has(nt)) pools.set(nt, new ParallelMultiplier(nt));
  return pools.get(nt);
}

async function timeMin(fn, reps) {
  await fn(); // 预热 JIT / 线程池
  let best = Infinity;
  for (let r = 0; r < reps; r++) {
    const t = process.hrtime.bigint();
    await fn();
    best = Math.min(best, Number(process.hrtime.bigint() - t) / 1e6);
  }
  return best;
}

(async () => {
  const hw = machineInfo();
  console.log(`机器: 物理核=${hw.physicalCores}, 逻辑核=${hw.logicalCores}, `
    + `L1d=${hw.cache.L1d >> 10}KB, L2=${hw.cache.L2 >> 10}KB, L3=${hw.cache.L3 >> 20}MB\n`);

  const manualDefault = { blockSize: 64, numThreads: 8 };
  const manualMax = { blockSize: 128, numThreads: hw.logicalCores };

  for (const s of SHAPES) {
    const flops = 2 * s.m * s.k * s.n;
    const reps = Math.max(3, Math.min(2000, Math.round(3e8 / Math.max(flops, 1))));
    const rng = makeRng(s.m * 1e6 + s.k * 1e3 + s.n);
    const A = createMatrix(s.m, s.k, rng);
    const B = createMatrix(s.k, s.n, rng);
    const C = createMatrix(s.m, s.n);

    const auto = recommendConfig(s.m, s.k, s.n);
    console.log(`=== ${s.name}  (flops=${flops.toExponential(1)}, reps=${reps}) ===`);
    console.log(`  推荐: blockSize=${auto.blockSize}, numThreads=${auto.numThreads}`);
    for (const r of auto.reasons) console.log(`    - ${r}`);

    const tAuto = await timeMin(() => matmulAuto(A, B, C), reps);
    const tManDef = await timeMin(
      () => getPool(manualDefault.numThreads).multiply(A, B, C, manualDefault.blockSize), reps);
    const tManMax = await timeMin(
      () => getPool(manualMax.numThreads).multiply(A, B, C, manualMax.blockSize), reps);

    // 正确性：自动结果必须与串行分块（同 bs）逐位一致
    const Cser = createMatrix(s.m, s.n);
    matmulSerialBlocked(A, B, Cser, auto.blockSize);
    await matmulAuto(A, B, C);
    const d = maxAbsDiff(C, Cser);

    console.log(`  耗时(最小值, ms): 自动=${tAuto.toFixed(3)}  `
      + `手工(bs=64,t=8)=${tManDef.toFixed(3)}  手工(bs=128,t=${hw.logicalCores})=${tManMax.toFixed(3)}`);
    console.log(`  自动 vs 手工默认: ${(tManDef / tAuto).toFixed(2)}x, `
      + `vs 手工激进: ${(tManMax / tAuto).toFixed(2)}x  | 一致性 max|diff|=${d} ${d === 0 ? '(逐位相等)' : '(不一致!)'}\n`);
  }

  await closeAutoPools();
  await Promise.all([...pools.values()].map(p => p.close()));
})().catch(e => { console.error(e); process.exit(1); });
