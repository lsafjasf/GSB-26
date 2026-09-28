'use strict';

const os = require('os');
const path = require('path');
const { Worker } = require('worker_threads');
const { Matrix, assertMultiplicable } = require('./matrix');
const { blockedSerialMultiply, blockTileCount, multiplyBlockShard } = require('./kernel');

const WORKER_PATH = path.join(__dirname, 'matmul-worker.js');

function describeMatrix(matrix) {
  return {
    rows: matrix.rows,
    cols: matrix.cols,
    buffer: matrix.buffer,
    byteOffset: matrix.byteOffset
  };
}

class MatMulPool {
  constructor(maxWorkers = Math.max(1, os.availableParallelism ? os.availableParallelism() : os.cpus().length)) {
    if (!Number.isSafeInteger(maxWorkers) || maxWorkers < 1) {
      throw new RangeError('maxWorkers must be a positive safe integer');
    }
    this.maxWorkers = maxWorkers;
    this.workers = [];
    this.idleWorkers = [];
    this.busy = false;
    this.destroyed = false;
    this.nextJobId = 0;
  }

  _pruneWorker(worker) {
    const index = this.workers.indexOf(worker);
    if (index !== -1) {
      this.workers.splice(index, 1);
    }
    const idleIndex = this.idleWorkers.indexOf(worker);
    if (idleIndex !== -1) {
      this.idleWorkers.splice(idleIndex, 1);
    }
  }

  _createWorker() {
    const worker = new Worker(WORKER_PATH);
    worker.on('error', () => {});
    worker.once('exit', () => {
      if (this.destroyed) {
        return;
      }
      this._pruneWorker(worker);
    });
    return new Promise((resolve, reject) => {
      const onOnline = () => {
        worker.removeListener('error', onError);
        this.workers.push(worker);
        resolve(worker);
      };
      const onError = (error) => {
        worker.removeListener('online', onOnline);
        reject(error);
      };
      worker.once('online', onOnline);
      worker.once('error', onError);
    });
  }

  async _ensureWorkers(count) {
    while (this.workers.length < count) {
      const worker = await this._createWorker();
      this.idleWorkers.push(worker);
    }
  }

  async multiply(a, b, options = {}) {
    if (this.destroyed) {
      throw new Error('this matrix multiplication pool has been destroyed');
    }
    if (this.busy) {
      throw new Error('this pool does not support concurrent multiply calls');
    }
    assertMultiplicable(a, b);
    const blockSize = options.blockSize === undefined ? 32 : options.blockSize;
    if (!Number.isSafeInteger(blockSize) || blockSize < 1) {
      throw new RangeError('blockSize must be a positive safe integer');
    }

    const requestedWorkers = options.workers === undefined ? this.maxWorkers : options.workers;
    if (!Number.isSafeInteger(requestedWorkers) || requestedWorkers < 1) {
      throw new RangeError('workers must be a positive safe integer');
    }

    const c = new Matrix(a.rows, b.cols);
    if (requestedWorkers <= 1) {
      multiplyBlockShard(a, b, c, { blockSize, shardIndex: 0, shardCount: 1 });
      return c;
    }

    const tileCount = blockTileCount(a.rows, b.cols, blockSize);
    const workerCount = Math.min(this.maxWorkers, requestedWorkers, tileCount);

    if (workerCount <= 1) {
      multiplyBlockShard(a, b, c, { blockSize, shardIndex: 0, shardCount: 1 });
      return c;
    }

    this.busy = true;
    const jobId = this.nextJobId;
    this.nextJobId += 1;
    try {
      await this._ensureWorkers(workerCount);
    } catch (error) {
      this.busy = false;
      throw error;
    }

    let settled = false;

    return await new Promise((resolve, reject) => {
      let completed = 0;

      const finish = (error) => {
        if (settled) {
          return;
        }
        settled = true;
        cleanup();
        this.busy = false;
        if (error) {
          reject(error);
        } else {
          resolve(c);
        }
      };

      const onMessage = (message, worker) => {
        if (message.jobId !== jobId) {
          return;
        }
        if (message.type !== 'done') {
          const error = new Error(message.message || 'worker matrix multiplication failed');
          error.name = message.name || 'WorkerError';
          finish(error);
          return;
        }

        completed += 1;
        if (completed === workerCount) {
          finish(null);
        }
      };

      const onError = (error) => finish(error);
      const onExit = (code) => {
        if (!settled) {
          finish(new Error(`worker stopped unexpectedly with exit code ${code}`));
        }
      };

      const cleanup = () => {
        for (const worker of activeWorkers) {
          worker.off('message', wrappedHandlers.get(worker));
          worker.off('error', onError);
          worker.off('exit', onExit);
          if (!this.destroyed && this.workers.includes(worker)) {
            this.idleWorkers.push(worker);
          }
        }
      };

      const activeWorkers = [];
      const wrappedHandlers = new Map();

      for (let shardIndex = 0; shardIndex < workerCount; shardIndex += 1) {
        const worker = this.idleWorkers.pop();
        activeWorkers.push(worker);
        const handler = (message) => onMessage(message, worker);
        wrappedHandlers.set(worker, handler);
        worker.on('message', handler);
        worker.on('error', onError);
        worker.on('exit', onExit);
        worker.postMessage({
          type: 'run',
          jobId,
          a: describeMatrix(a),
          b: describeMatrix(b),
          c: describeMatrix(c),
          blockSize,
          shardIndex,
          shardCount: workerCount
        });
      }
    });
  }

  async destroy() {
    this.destroyed = true;
    await Promise.all(
      this.workers.map(
        (worker) =>
          new Promise((resolve) => {
            worker.once('exit', resolve);
            worker.postMessage({ type: 'stop' });
          })
      )
    );
    this.workers = [];
  }
}

async function multiplyParallel(a, b, options = {}) {
  const workers =
    options.workers === undefined
      ? Math.max(1, os.availableParallelism ? os.availableParallelism() : os.cpus().length)
      : options.workers;
  const pool = new MatMulPool(workers);
  try {
    return await pool.multiply(a, b, options);
  } finally {
    await pool.destroy();
  }
}

module.exports = {
  MatMulPool,
  multiplyParallel,
  blockedSerialMultiply
};
