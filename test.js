'use strict';
/**
 * test.js — 串并行一致性与形状健壮性自测。
 *
 * 断言分两级：
 *  1) 并行 vs 串行分块：要求逐位相等（容差 0），因为两条路径累加顺序完全相同；
 *  2) 分块 vs 朴素参照：允许浮点容差，因为分块改变了求和顺序，
 *     误差上界约为 O(k * eps * max|a||b|)，eps = 2^-53 ≈ 1.1e-16。
 */
const { createMatrix, matmulNaive, matmulSerialBlocked, ParallelMultiplier } = require('./matmul');

const EPS = 2 ** -52; // double 机器精度

// 可复现的伪随机数（xorshift32）
function makeRng(seed) {
  let s = seed >>> 0 || 1;
  return () => {
    s ^= s << 13; s >>>= 0;
    s ^= s >> 17;
    s ^= s << 5; s >>>= 0;
    return s / 0xffffffff * 2 - 1; // [-1, 1)
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

function maxAbs(M) {
  let m = 0;
  for (const v of M.data) { const a = Math.abs(v); if (a > m) m = a; }
  return m;
}

let passed = 0, failed = 0;
function check(name, cond, detail = '') {
  if (cond) { passed++; console.log(`  PASS  ${name}${detail ? '  (' + detail + ')' : ''}`); }
  else { failed++; console.error(`  FAIL  ${name}  ${detail}`); }
}

async function runCase(pool, { name, m, k, n, blockSize, fillA, fillB }) {
  const rng = makeRng(m * 1e6 + k * 1e3 + n);
  const A = createMatrix(m, k, fillA || (() => rng()));
  const B = createMatrix(k, n, fillB || (() => rng()));
  const Cref = createMatrix(m, n);
  const Cser = createMatrix(m, n);
  const Cpar = createMatrix(m, n);

  matmulNaive(A, B, Cref);
  matmulSerialBlocked(A, B, Cser, blockSize);
  await pool.multiply(A, B, Cpar, blockSize);

  // 1) 并行 vs 串行分块：逐位相等
  const dPS = maxAbsDiff(Cpar, Cser);
  check(`${name}: 并行==串行(逐位)`, dPS === 0, `max|diff|=${dPS}`);

  // 2) 分块 vs 朴素：容差 = 8*k*eps*max|A|*max|B|（保守上界，留 8 倍余量）
  const tol = Math.max(8 * k * EPS * maxAbs(A) * maxAbs(B), 1e-300);
  const dSN = maxAbsDiff(Cser, Cref);
  check(`${name}: 分块≈朴素(容差)`, dSN <= tol, `max|diff|=${dSN.toExponential(2)}, tol=${tol.toExponential(2)}`);
}

(async () => {
  const pool = new ParallelMultiplier(8);
  const zero = () => 0;
  const one = () => 1;

  const cases = [
    { name: '单个元素 1x1*1x1', m: 1, k: 1, n: 1, blockSize: 64 },
    { name: '块大于矩阵 3x3 bs=64', m: 3, k: 3, n: 3, blockSize: 64 },
    { name: '非整块 127x131*131x113 bs=32', m: 127, k: 131, n: 113, blockSize: 32 },
    { name: '非整块 bs=1', m: 17, k: 19, n: 13, blockSize: 1 },
    { name: '极扁 2x400*400x3', m: 2, k: 400, n: 3, blockSize: 16 },
    { name: '极长 400x2*2x400', m: 400, k: 2, n: 400, blockSize: 16 },
    { name: '行向量点积 1x256*256x1', m: 1, k: 256, n: 1, blockSize: 32 },
    { name: '外积 256x1*1x256', m: 256, k: 1, n: 256, blockSize: 32 },
    { name: '零矩阵 64x64*64x64', m: 64, k: 64, n: 64, blockSize: 16, fillA: zero, fillB: zero },
    { name: '零乘非零 0x5*5x3', m: 0, k: 5, n: 3, blockSize: 8 },
    { name: '单位矩阵 33x33*33x31', m: 33, k: 33, n: 31, blockSize: 8,
      fillA: (i, j) => (i === j ? 1 : 0) },
    { name: '大方阵 512^3 bs=64', m: 512, k: 512, n: 512, blockSize: 64 },
    { name: '不规则大矩阵 1000x777*777x999 bs=48', m: 1000, k: 777, n: 999, blockSize: 48 },
  ];

  for (const c of cases) await runCase(pool, c);

  // 单线程池也应与串行逐位一致
  const pool1 = new ParallelMultiplier(1);
  const rng = makeRng(42);
  const A = createMatrix(100, 90, () => rng());
  const B = createMatrix(90, 110, () => rng());
  const Cs = createMatrix(100, 110), Cp = createMatrix(100, 110);
  matmulSerialBlocked(A, B, Cs, 24);
  await pool1.multiply(A, B, Cp, 24);
  check('单线程池: 并行==串行(逐位)', maxAbsDiff(Cp, Cs) === 0);
  await pool1.close();

  // 非法维度应抛错而非未定义行为
  let threw = false;
  try { matmulSerialBlocked(createMatrix(2, 3), createMatrix(4, 5), createMatrix(2, 5)); }
  catch (e) { threw = e instanceof RangeError; }
  check('维度不匹配抛 RangeError', threw);

  await pool.close();
  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
