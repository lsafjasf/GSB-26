'use strict';
/** worker.js — 并行瓦片消费者。所有矩阵经 SharedArrayBuffer 共享，零拷贝。 */
const { parentPort } = require('worker_threads');
const { blockMulAdd } = require('./matmul');

parentPort.on('message', task => {
  const a = new Float64Array(task.aBuf);
  const b = new Float64Array(task.bBuf);
  const c = new Float64Array(task.cBuf);
  const counter = new Int32Array(task.counterBuf);
  const { k, n, blockSize, tiles } = task;
  for (;;) {
    const t = Atomics.add(counter, 0, 1); // 动态领取瓦片，自动负载均衡
    if (t >= tiles.length) break;
    const [i0, i1, j0, j1] = tiles[t];
    for (let kk = 0; kk < k; kk += blockSize) {
      blockMulAdd(a, k, b, n, c, n, i0, i1, j0, j1, kk, Math.min(kk + blockSize, k));
    }
  }
  parentPort.postMessage('done');
});
