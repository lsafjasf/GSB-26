'use strict';

const SHARED_MEMORY = typeof SharedArrayBuffer === 'function' ? SharedArrayBuffer : ArrayBuffer;

function isNonNegativeSafeInteger(value) {
  return Number.isSafeInteger(value) && value >= 0;
}

function isPositiveSafeInteger(value) {
  return Number.isSafeInteger(value) && value > 0;
}

class Matrix {
  constructor(rows, cols, buffer = null, byteOffset = 0) {
    if (!isNonNegativeSafeInteger(rows) || !isNonNegativeSafeInteger(cols)) {
      throw new TypeError('rows and cols must be non-negative safe integers');
    }
    if (!Number.isSafeInteger(rows * cols)) {
      throw new RangeError('matrix dimensions are too large');
    }

    this.rows = rows;
    this.cols = cols;
    const length = rows * cols;
    const byteLength = length * Float64Array.BYTES_PER_ELEMENT;

    if (buffer === null) {
      this.buffer = new SHARED_MEMORY(byteLength);
      this.byteOffset = 0;
    } else {
      const isSupportedBuffer =
        buffer instanceof ArrayBuffer ||
        (typeof SharedArrayBuffer === 'function' && buffer instanceof SharedArrayBuffer);
      if (!isSupportedBuffer) {
        throw new TypeError('buffer must be an ArrayBuffer or SharedArrayBuffer');
      }
      if (!Number.isSafeInteger(byteOffset) || byteOffset < 0) {
        throw new RangeError('byteOffset must be a non-negative safe integer');
      }
      if (byteOffset % Float64Array.BYTES_PER_ELEMENT !== 0) {
        throw new RangeError('byteOffset must be 8-byte aligned');
      }
      if (buffer.byteLength - byteOffset < byteLength) {
        throw new RangeError('buffer is too small for the requested matrix dimensions');
      }
      this.buffer = buffer;
      this.byteOffset = byteOffset;
    }

    this.data = new Float64Array(this.buffer, this.byteOffset, length);
  }

  static fromNested(values) {
    if (!Array.isArray(values)) {
      throw new TypeError('values must be an array of rows');
    }
    const rows = values.length;
    const cols = rows === 0 ? 0 : values[0].length;
    if (!values.every((row) => Array.isArray(row) && row.length === cols)) {
      throw new TypeError('all rows must be arrays with the same length');
    }
    const matrix = new Matrix(rows, cols);
    for (let i = 0; i < rows; i += 1) {
      for (let j = 0; j < cols; j += 1) {
        const value = values[i][j];
        if (typeof value !== 'number' || !Number.isFinite(value)) {
          throw new TypeError('matrix entries must be finite numbers');
        }
        matrix.data[i * cols + j] = value;
      }
    }
    return matrix;
  }

  get(row, col) {
    if (row < 0 || row >= this.rows || col < 0 || col >= this.cols) {
      throw new RangeError('matrix index is out of bounds');
    }
    return this.data[row * this.cols + col];
  }

  set(row, col, value) {
    if (row < 0 || row >= this.rows || col < 0 || col >= this.cols) {
      throw new RangeError('matrix index is out of bounds');
    }
    if (typeof value !== 'number' || !Number.isFinite(value)) {
      throw new TypeError('matrix entries must be finite numbers');
    }
    this.data[row * this.cols + col] = value;
  }

  toNested() {
    const result = new Array(this.rows);
    for (let i = 0; i < this.rows; i += 1) {
      const row = new Array(this.cols);
      const offset = i * this.cols;
      for (let j = 0; j < this.cols; j += 1) {
        row[j] = this.data[offset + j];
      }
      result[i] = row;
    }
    return result;
  }

  clone() {
    const copy = new Matrix(this.rows, this.cols);
    copy.data.set(this.data);
    return copy;
  }
}

function assertMultiplicable(a, b) {
  if (!(a instanceof Matrix) || !(b instanceof Matrix)) {
    throw new TypeError('multiplication operands must be Matrix instances');
  }
  if (a.cols !== b.rows) {
    throw new RangeError(
      `matrix shapes are incompatible: (${a.rows}x${a.cols}) * (${b.rows}x${b.cols})`
    );
  }
}

module.exports = {
  Matrix,
  assertMultiplicable,
  isPositiveSafeInteger
};
