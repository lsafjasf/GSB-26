'use strict';

const { Matrix, assertMultiplicable, isPositiveSafeInteger } = require('./matrix');

function validateBlockSize(blockSize) {
  if (!isPositiveSafeInteger(blockSize)) {
    throw new TypeError('blockSize must be a positive safe integer');
  }
}

function multiplyTile(a, b, c, tile) {
  const { i0, i1, j0, j1 } = tile;
  const kBlock = tile.kBlock === undefined ? TASK_BLOCK : tile.kBlock;
  const shared = a.cols;
  const bCols = b.cols;

  for (let k0 = 0; k0 < shared; k0 += kBlock) {
    const k1 = Math.min(k0 + kBlock, shared);
    for (let i = i0; i < i1; i += 1) {
      const aRow = i * shared;
      const cRow = i * bCols;
      for (let k = k0; k < k1; k += 1) {
        const aik = a.data[aRow + k];
        const bRow = k * bCols;
        const multiplier = aik;
        for (let j = j0; j < j1; j += 1) {
          c.data[cRow + j] += multiplier * b.data[bRow + j];
        }
      }
    }
  }
}

const TASK_BLOCK = 32;

function blockedMultiply(a, b, c, blockSize) {
  validateBlockSize(blockSize);
  multiplyBlockShard(a, b, c, { blockSize, shardIndex: 0, shardCount: 1 });
}

function multiplyBlockShard(a, b, c, options) {
  const blockSize = options.blockSize;
  const shardIndex = options.shardIndex;
  const shardCount = options.shardCount;
  validateBlockSize(blockSize);
  if (!Number.isSafeInteger(shardIndex) || shardIndex < 0) {
    throw new RangeError('shardIndex must be a non-negative safe integer');
  }
  if (!Number.isSafeInteger(shardCount) || shardCount <= 0 || shardIndex >= shardCount) {
    throw new RangeError('shardCount must be a positive integer greater than shardIndex');
  }

  const rows = a.rows;
  const cols = b.cols;
  const tileRows = Math.ceil(rows / blockSize);
  const tileCols = Math.ceil(cols / blockSize);
  const tileCount = tileRows * tileCols;

  for (let tile = shardIndex; tile < tileCount; tile += shardCount) {
    const tileRow = Math.floor(tile / tileCols);
    const tileCol = tile % tileCols;
    const i0 = tileRow * blockSize;
    const j0 = tileCol * blockSize;
    const i1 = Math.min(i0 + blockSize, rows);
    const j1 = Math.min(j0 + blockSize, cols);
    multiplyTile(a, b, c, { i0, i1, j0, j1, kBlock: blockSize });
  }
}

function blockTileCount(rows, cols, blockSize) {
  validateBlockSize(blockSize);
  return Math.ceil(rows / blockSize) * Math.ceil(cols / blockSize);
}

function naiveMultiply(a, b) {
  assertMultiplicable(a, b);
  const c = new Matrix(a.rows, b.cols);
  const shared = a.cols;
  const bCols = b.cols;
  for (let i = 0; i < a.rows; i += 1) {
    const aRow = i * shared;
    const cRow = i * bCols;
    for (let k = 0; k < shared; k += 1) {
      const aik = a.data[aRow + k];
      const bRow = k * bCols;
      for (let j = 0; j < bCols; j += 1) {
        c.data[cRow + j] += aik * b.data[bRow + j];
      }
    }
  }
  return c;
}

function reverseOrderReferenceMultiply(a, b) {
  assertMultiplicable(a, b);
  const c = new Matrix(a.rows, b.cols);
  for (let i = 0; i < a.rows; i += 1) {
    for (let j = 0; j < b.cols; j += 1) {
      let sum = 0;
      for (let k = a.cols - 1; k >= 0; k -= 1) {
        sum += a.data[i * a.cols + k] * b.data[k * b.cols + j];
      }
      c.data[i * b.cols + j] = sum;
    }
  }
  return c;
}

function blockedSerialMultiply(a, b, blockSize = 32) {
  assertMultiplicable(a, b);
  validateBlockSize(blockSize);
  const c = new Matrix(a.rows, b.cols);
  blockedMultiply(a, b, c, blockSize);
  return c;
}

module.exports = {
  blockedMultiply,
  blockedSerialMultiply,
  blockTileCount,
  multiplyBlockShard,
  multiplyTile,
  naiveMultiply,
  reverseOrderReferenceMultiply,
  validateBlockSize
};
