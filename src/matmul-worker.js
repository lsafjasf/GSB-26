'use strict';

const { parentPort } = require('worker_threads');
const { Matrix } = require('./matrix');
const { multiplyBlockShard } = require('./kernel');

parentPort.on('message', (message) => {
  if (message.type === 'stop') {
    parentPort.close();
    return;
  }

  if (message.type !== 'run') {
    return;
  }

  try {
    const { a: aInfo, b: bInfo, c: cInfo, blockSize, shardIndex, shardCount, jobId } = message;
    const a = new Matrix(aInfo.rows, aInfo.cols, aInfo.buffer, aInfo.byteOffset);
    const b = new Matrix(bInfo.rows, bInfo.cols, bInfo.buffer, bInfo.byteOffset);
    const c = new Matrix(cInfo.rows, cInfo.cols, cInfo.buffer, cInfo.byteOffset);

    multiplyBlockShard(a, b, c, { blockSize, shardIndex, shardCount });
    parentPort.postMessage({ type: 'done', jobId });
  } catch (error) {
    parentPort.postMessage({
      type: 'error',
      jobId: message.jobId,
      name: error.name,
      message: error.message,
      stack: error.stack
    });
  }
});
