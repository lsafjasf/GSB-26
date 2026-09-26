'use strict';

const assert = require('assert');
const { Matrix } = require('../src/matrix');
const {
  blockedSerialMultiply,
  naiveMultiply,
  reverseOrderReferenceMultiply
} = require('../src/kernel');
const { MatMulPool } = require('../src/parallel-matmul');

const EPSILON = Number.EPSILON;

function mulberry32(seed) {
  return function random() {
    let value = (seed += 0x6d2b79f5);
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  };
}

function randomMatrix(rows, cols, seed, scale = 1) {
  const random = mulberry32(seed);
  const matrix = new Matrix(rows, cols);
  for (let index = 0; index < matrix.data.length; index += 1) {
    matrix.data[index] = (random() * 2 - 1) * scale;
  }
  return matrix;
}

function zeros(rows, cols) {
  return new Matrix(rows, cols);
}

function compareTolerance(a, b) {
  assert.strictEqual(a.rows, b.rows);
  assert.strictEqual(a.cols, b.cols);
  const shared = a.cols;
  let maxA = 0;
  let maxB = 0;
  for (const value of a.data) maxA = Math.max(maxA, Math.abs(value));
  for (const value of b.data) maxB = Math.max(maxB, Math.abs(value));

  const scale = 1 + Math.max(maxA, maxB);
  const atol = 16 * EPSILON * Math.max(1, shared) * scale;
  const rtol = 16 * EPSILON * shared;
  let maxAbsolute = 0;
  let maxRelative = 0;

  for (let index = 0; index < a.data.length; index += 1) {
    const expected = b.data[index];
    const actual = a.data[index];
    const absolute = Math.abs(actual - expected);
    const relativeDenominator = Math.max(Math.abs(actual), Math.abs(expected), 1);
    maxAbsolute = Math.max(maxAbsolute, absolute);
    maxRelative = Math.max(maxRelative, absolute / relativeDenominator);
    assert.ok(
      absolute <= atol + rtol * Math.max(Math.abs(actual), Math.abs(expected)),
      `element ${index} differs: actual=${actual}, expected=${expected}, maxAbs=${absolute}`
    );
  }
  return { maxAbsolute, maxRelative, atol, rtol };
}

function assertBitwiseEqual(a, b) {
  assert.strictEqual(a.rows, b.rows);
  assert.strictEqual(a.cols, b.cols);
  assert.strictEqual(a.data.length, b.data.length);
  for (let index = 0; index < a.data.length; index += 1) {
    assert.ok(
      Object.is(a.data[index], b.data[index]),
      `element ${index} differs: ${a.data[index]} !== ${b.data[index]}`
    );
  }
  assert.notStrictEqual(a.buffer, b.buffer);
}

const shapes = [
  { m: 1, k: 1, n: 1, seed: 101 },
  { m: 1, k: 301, n: 5, seed: 102 },
  { m: 3, k: 333, n: 2, seed: 103 },
  { m: 301, k: 1, n: 37, seed: 104 },
  { m: 333, k: 2, n: 7, seed: 105 },
  { m: 37, k: 31, n: 29, seed: 106 },
  { m: 65, k: 33, n: 129, seed: 107 },
  { m: 17, k: 64, n: 19, seed: 108 },
  { m: 3, k: 0, n: 5, seed: 109 },
  { m: 0, k: 0, n: 5, seed: 110 },
  { m: 5, k: 0, n: 0, seed: 111 }
];

const blockSizes = [1, 7, 13, 31, 32, 64, 129];

async function main() {
  const pool = new MatMulPool(4);
  let checked = 0;

  try {
    for (const shape of shapes) {
      for (const blockSize of blockSizes) {
        const a = randomMatrix(shape.m, shape.k, shape.seed + blockSize);
        const b = randomMatrix(shape.k, shape.n, shape.seed + 1000 + blockSize);
        const serial = blockedSerialMultiply(a, b, blockSize);

        for (const workers of [1, 2, 3, 4]) {
          const parallel = await pool.multiply(a, b, { workers, blockSize });
          assertBitwiseEqual(parallel, serial);
          checked += 1;
        }
      }
    }

    const regular = randomMatrix(47, 53, 9001, 3);
    const regularB = randomMatrix(53, 41, 9002, 3);
    const naive = naiveMultiply(regular, regularB);
    const reverse = reverseOrderReferenceMultiply(regular, regularB);
    const parallel = await pool.multiply(regular, regularB, { workers: 4, blockSize: 17 });
    const statistics = compareTolerance(parallel, reverse);
    assertBitwiseEqual(parallel, blockedSerialMultiply(regular, regularB, 17));
    compareTolerance(naive, reverse);
    console.log(
      `reverse-order reference max abs difference: ${statistics.maxAbsolute.toExponential(3)}`
    );

    const zeroA = zeros(10, 11);
    const randomB = randomMatrix(11, 9, 700);
    const zeroProduct = await pool.multiply(zeroA, randomB, { workers: 4, blockSize: 7 });
    assert.strictEqual(zeroProduct.rows, 10);
    assert.strictEqual(zeroProduct.cols, 9);
    for (const value of zeroProduct.data) assert.strictEqual(value, 0);

    const one = Matrix.fromNested([[3.5]]);
    const oneB = Matrix.fromNested([[2]]);
    const oneProduct = await pool.multiply(one, oneB, { workers: 4, blockSize: 1 });
    assert.strictEqual(oneProduct.get(0, 0), 7);

    const emptyA = new Matrix(0, 3);
    const emptyB = new Matrix(3, 8);
    const emptyProduct = await pool.multiply(emptyA, emptyB, { workers: 4 });
    assert.strictEqual(emptyProduct.data.length, 0);

    assert.throws(() => blockedSerialMultiply(new Matrix(2, 3), new Matrix(2, 2)));
    assert.throws(() => blockedSerialMultiply(regular, regularB, 0));
    await assert.rejects(() => pool.multiply(regular, regularB, { workers: 0 }));
    await assert.rejects(() => pool.multiply(new Matrix(2, 3), new Matrix(2, 2)));

    const first = pool.multiply(regular, regularB, { workers: 2, blockSize: 16 });
    await assert.rejects(() => pool.multiply(regular, regularB, { workers: 2, blockSize: 16 }));
    await first;

    console.log(`all correctness checks passed: ${checked} serial/parallel shape-block-worker combinations`);
  } finally {
    await pool.destroy();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
