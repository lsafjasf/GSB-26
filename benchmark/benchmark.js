'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const { performance } = require('perf_hooks');
const { Matrix } = require('../src/matrix');
const { blockedSerialMultiply, naiveMultiply } = require('../src/kernel');
const { MatMulPool } = require('../src/parallel-matmul');

const availableCores = os.availableParallelism ? os.availableParallelism() : os.cpus().length;
const physicalCoreCap = Math.min(availableCores, 8);
const n = Number(process.env.BENCH_N || 384);
const repetitions = Number(process.env.BENCH_REPS || 3);
const maxWorkers = Number(process.env.BENCH_MAX_WORKERS || physicalCoreCap);
const includeSmt = process.env.BENCH_INCLUDE_SMT === '1';
const outputDir = path.join(__dirname, 'results');

const blockSizes = [...new Set([4, 8, 16, 24, 32, 48, 64, 96, 128, 192, 256, n])]
  .filter((size) => Number.isSafeInteger(size) && size >= 1 && size <= n)
  .sort((x, y) => x - y);

function mulberry32(seed) {
  return function random() {
    let value = (seed += 0x6d2b79f5);
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  };
}

function randomMatrix(rows, cols, seed) {
  const random = mulberry32(seed);
  const matrix = new Matrix(rows, cols);
  for (let index = 0; index < matrix.data.length; index += 1) {
    matrix.data[index] = random() * 2 - 1;
  }
  return matrix;
}

function median(values) {
  const sorted = values.slice().sort((x, y) => x - y);
  return sorted[Math.floor(sorted.length / 2)];
}

async function timeRuns(createResult, runs) {
  const values = [];
  let sink = 0;
  for (let run = 0; run < runs; run += 1) {
    const start = performance.now();
    const result = await createResult();
    const elapsed = performance.now() - start;
    sink += result.data[0] + result.data[result.data.length - 1];
    values.push(elapsed);
  }
  return { median: median(values), runs: values, sink };
}

function gflops(ms, size) {
  return (2 * size * size * size) / (ms * 1e6);
}

function fixed(value, digits = 3) {
  return value.toFixed(digits);
}

function escapeXml(value) {
  return String(value).replace(/[<>&"]/g, (char) => {
    const map = { '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;' };
    return map[char];
  });
}

function lineChart(config) {
  const width = 780;
  const height = 440;
  const left = 72;
  const right = 24;
  const top = 48;
  const bottom = 72;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  const yMax = config.yMax === undefined ? Math.max(...config.series.flatMap((series) => series.values)) : config.yMax;
  const xPosition = (index) => left + (index * plotWidth) / Math.max(1, config.xLabels.length - 1);
  const yPosition = (value) => top + plotHeight - (value / yMax) * plotHeight;

  const lines = [
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">`,
    '<rect width="100%" height="100%" fill="white"/>',
    `<text x="${width / 2}" y="26" text-anchor="middle" font-family="sans-serif" font-size="18" font-weight="bold">${escapeXml(config.title)}</text>`
  ];

  for (let tick = 0; tick <= 4; tick += 1) {
    const value = (yMax * tick) / 4;
    const y = yPosition(value);
    lines.push(`<line x1="${left}" y1="${y}" x2="${width - right}" y2="${y}" stroke="#e5e7eb"/>`);
    lines.push(`<text x="${left - 10}" y="${y + 4}" text-anchor="end" font-family="sans-serif" font-size="12">${escapeXml(fixed(value, 2))}</text>`);
  }

  lines.push(`<line x1="${left}" y1="${top}" x2="${left}" y2="${top + plotHeight}" stroke="#374151"/>`);
  lines.push(`<line x1="${left}" y1="${top + plotHeight}" x2="${width - right}" y2="${top + plotHeight}" stroke="#374151"/>`);

  config.xLabels.forEach((label, index) => {
    const x = xPosition(index);
    lines.push(`<line x1="${x}" y1="${top + plotHeight}" x2="${x}" y2="${top + plotHeight + 5}" stroke="#374151"/>`);
    lines.push(`<text x="${x}" y="${top + plotHeight + 22}" text-anchor="middle" font-family="sans-serif" font-size="12">${escapeXml(label)}</text>`);
  });

  lines.push(`<text x="${left + plotWidth / 2}" y="${height - 18}" text-anchor="middle" font-family="sans-serif" font-size="14">${escapeXml(config.xLabel)}</text>`);
  lines.push(`<text transform="translate(20 ${top + plotHeight / 2}) rotate(-90)" text-anchor="middle" font-family="sans-serif" font-size="14">${escapeXml(config.yLabel)}</text>`);

  config.series.forEach((series, seriesIndex) => {
    const points = series.values
      .map((value, index) => `${xPosition(index)},${yPosition(value)}`)
      .join(' ');
    const dash = series.dash ? ` stroke-dasharray="${series.dash}"` : '';
    lines.push(`<polyline fill="none" stroke="${series.color}" stroke-width="3"${dash} points="${points}"/>`);
    series.values.forEach((value, index) => {
      lines.push(`<circle cx="${xPosition(index)}" cy="${yPosition(value)}" r="4" fill="${series.color}"/>`);
    });
    const legendX = width - right - 180;
    const legendY = top + 18 + seriesIndex * 22;
    lines.push(`<line x1="${legendX}" y1="${legendY}" x2="${legendX + 26}" y2="${legendY}" stroke="${series.color}" stroke-width="3"${dash}/>`);
    lines.push(`<text x="${legendX + 34}" y="${legendY + 4}" font-family="sans-serif" font-size="13">${escapeXml(series.name)}</text>`);
  });

  lines.push('</svg>');
  return lines.join('\n');
}

async function main() {
  fs.mkdirSync(outputDir, { recursive: true });
  const a = randomMatrix(n, n, 1234);
  const b = randomMatrix(n, n, 5678);

  if (!(a.buffer instanceof SharedArrayBuffer && b.buffer instanceof SharedArrayBuffer)) {
    throw new Error('inputs must use SharedArrayBuffer for zero-copy worker sharing');
  }

  const serialCheck = blockedSerialMultiply(a, b, 32);
  const warmupPool = new MatMulPool(maxWorkers);
  const parallelCheck = await warmupPool.multiply(a, b, { workers: maxWorkers, blockSize: 32 });
  for (let index = 0; index < serialCheck.data.length; index += 1) {
    if (!Object.is(serialCheck.data[index], parallelCheck.data[index])) {
      throw new Error(`benchmark correctness failed at index ${index}`);
    }
  }

  const warmupA = randomMatrix(128, 128, 2000);
  const warmupB = randomMatrix(128, 128, 3000);
  blockedSerialMultiply(warmupA, warmupB, 32);
  await warmupPool.multiply(warmupA, warmupB, { workers: maxWorkers, blockSize: 32 });

  const blockRows = [];
  let sink = 0;
  for (const blockSize of blockSizes) {
    process.stdout.write(`block ${blockSize} ... `);
    const serial = await timeRuns(() => blockedSerialMultiply(a, b, blockSize), repetitions);
    const parallel = await timeRuns(
      () => warmupPool.multiply(a, b, { workers: maxWorkers, blockSize }),
      repetitions
    );
    sink += serial.sink + parallel.sink;
    blockRows.push({ blockSize, serial, parallel });
    process.stdout.write(`${fixed(serial.median)} ms / ${fixed(parallel.median)} ms\n`);
  }

  const bestBlockRow = blockRows.reduce((best, row) =>
    row.parallel.median < best.parallel.median ||
    (row.parallel.median === best.parallel.median && row.serial.median < best.serial.median)
      ? row
      : best
  );
  const bestBlock = bestBlockRow.blockSize;
  const serialMs = bestBlockRow.serial.median;

  const threadCounts = [];
  for (let count = 1; count <= maxWorkers; count *= 2) {
    threadCounts.push(count);
  }
  if (!threadCounts.includes(maxWorkers)) {
    threadCounts.push(maxWorkers);
  }
  if (includeSmt && availableCores > maxWorkers) {
    threadCounts.push(availableCores);
  }

  const speedupRows = [];
  for (const workers of threadCounts) {
    process.stdout.write(`workers ${workers} ... `);
    const result = await timeRuns(
      () => warmupPool.multiply(a, b, { workers, blockSize: bestBlock }),
      repetitions
    );
    sink += result.sink;
    const speedup = serialMs / result.median;
    speedupRows.push({ workers, result, speedup, efficiency: speedup / workers });
    process.stdout.write(`${fixed(result.median)} ms, speedup ${fixed(speedup, 2)}x\n`);
  }

  const naiveStart = performance.now();
  const naiveResult = naiveMultiply(a, b);
  const naiveMs = performance.now() - naiveStart;
  sink += naiveResult.data[0] + naiveResult.data[naiveResult.data.length - 1];

  const inputBytes = (a.data.length + b.data.length) * Float64Array.BYTES_PER_ELEMENT;
  const outputBytes = n * n * Float64Array.BYTES_PER_ELEMENT;
  const matrixBytes = inputBytes + outputBytes;
  const metadataBytes = 256;
  const memoryUpperBound = matrixBytes + maxWorkers * metadataBytes;

  const environment = {
    node: process.version,
    cpu: os.cpus()[0].model,
    availableCores,
    physicalCoreCap,
    benchmarkWorkers: maxWorkers,
    n,
    repetitions,
    bestBlock,
    sink,
    timestamp: new Date().toISOString()
  };

  const blockCsv = [
    'block_size,serial_ms,parallel_ms,parallel_workers,serial_gflops,parallel_gflops',
    ...blockRows.map((row) =>
      [
        row.blockSize,
        fixed(row.serial.median),
        fixed(row.parallel.median),
        maxWorkers,
        fixed(gflops(row.serial.median, n)),
        fixed(gflops(row.parallel.median, n))
      ].join(',')
    )
  ].join('\n');

  const speedupCsv = [
    'workers,blocked_serial_ms,parallel_ms,speedup,efficiency,parallel_gflops',
    ...speedupRows.map((row) =>
      [
        row.workers,
        fixed(serialMs),
        fixed(row.result.median),
        fixed(row.speedup),
        fixed(row.efficiency),
        fixed(gflops(row.result.median, n))
      ].join(',')
    )
  ].join('\n');

  const blockChart = lineChart({
    title: `Block size vs. median time (${n}x${n}, ${maxWorkers} workers)`,
    xLabels: blockRows.map((row) => String(row.blockSize)),
    xLabel: 'Block size (elements)',
    yLabel: 'Median time (ms)',
    series: [
      {
        name: 'Blocked serial',
        color: '#2563eb',
        values: blockRows.map((row) => row.serial.median)
      },
      {
        name: `Parallel (${maxWorkers} workers)`,
        color: '#dc2626',
        values: blockRows.map((row) => row.parallel.median)
      }
    ]
  });

  const speedupChart = lineChart({
    title: `Workers vs. speedup (${n}x${n}, block ${bestBlock})`,
    xLabels: speedupRows.map((row) => String(row.workers)),
    xLabel: 'Worker threads',
    yLabel: 'Speedup over blocked serial',
    yMax: speedupRows[speedupRows.length - 1].workers,
    series: [
      {
        name: 'Ideal linear',
        color: '#9ca3af',
        dash: '6 5',
        values: speedupRows.map((row) => row.workers)
      },
      {
        name: 'Measured speedup',
        color: '#dc2626',
        values: speedupRows.map((row) => row.speedup)
      }
    ]
  });

  const blockTable = blockRows
    .map(
      (row) =>
        `| ${row.blockSize} | ${fixed(row.serial.median)} | ${fixed(row.parallel.median)} | ${fixed(gflops(row.serial.median, n))} | ${fixed(gflops(row.parallel.median, n))} |`
    )
    .join('\n');

  const speedupTable = speedupRows
    .map(
      (row) =>
        `| ${row.workers} | ${fixed(row.result.median)} | ${fixed(row.speedup)} | ${fixed(row.efficiency * 100)}% |`
    )
    .join('\n');

  const report = `# Benchmark results

- Environment: Node ${environment.node}, ${environment.cpu}
- Reported cores: ${availableCores}; benchmark cap: ${maxWorkers} worker threads
- Shape: ${n}x${n} * ${n}x${n}; repetitions per point: ${repetitions}; statistic: median
- Best block size by parallel median time in this sweep: ${bestBlock}
- Naive i-k-j baseline: ${fixed(naiveMs)} ms

## Block-size curve

![Block-size curve](block-size.svg)

| Block | Serial ms | Parallel ms | Serial GFLOPS | Parallel GFLOPS |
|---:|---:|---:|---:|---:|
${blockTable}

Small blocks have more block-loop overhead and may under-use each cache line. Very large blocks stop fitting comfortably in L1/L2, so reused B and C values are evicted more often; they also shrink the tile count to ceil(M/B)*ceil(N/B), which can leave most workers idle. On this Node/i-k-j workload the unblocked serial run is competitive, but the parallel optimum is near ${bestBlock}: a block needs to be cache-friendly while still producing many more tiles than workers. Use that range as a starting point and tune once on the target CPU.

## Thread scaling

![Speedup curve](speedup.svg)

| Workers | Parallel ms | Speedup | Efficiency |
|---:|---:|---:|---:|
${speedupTable}

Speedup is sublinear because the machine has shared memory bandwidth and cache capacity, worker startup/message synchronization has fixed overhead, OS scheduling and SMT threads do not add equal execution capacity, and work is divided by output tiles so irregular shapes or a small number of tiles can leave load imbalance. When the requested worker count exceeds usable tiles, extra workers are deliberately not launched.

## Memory bound

For an MxK times KxN product, the library stores one C and shares A/B by SharedArrayBuffer. The floating-point payload is 8(MK + KN + MN) bytes: the caller's A and B plus exactly one output C, with no per-block copies. At this benchmark size that is ${(matrixBytes / 1024 / 1024).toFixed(2)} MiB. Workers do not receive block arrays; each worker receives constant-size metadata (approximately ${metadataBytes} bytes), giving a measured-configuration upper bound near ${(memoryUpperBound / 1024 / 1024).toFixed(2)} MiB plus Node runtime/thread stacks.
`;

  const results = {
    environment,
    blockRows: blockRows.map((row) => ({
      blockSize: row.blockSize,
      serialMs: row.serial.median,
      parallelMs: row.parallel.median
    })),
    speedupRows: speedupRows.map((row) => ({
      workers: row.workers,
      ms: row.result.median,
      speedup: row.speedup,
      efficiency: row.efficiency
    })),
    naiveMs,
    memory: {
      matrixBytes,
      metadataBytesPerWorker: metadataBytes,
      upperBoundBytes: memoryUpperBound
    }
  };

  fs.writeFileSync(path.join(outputDir, 'block-size.csv'), `${blockCsv}\n`);
  fs.writeFileSync(path.join(outputDir, 'speedup.csv'), `${speedupCsv}\n`);
  fs.writeFileSync(path.join(outputDir, 'block-size.svg'), blockChart);
  fs.writeFileSync(path.join(outputDir, 'speedup.svg'), speedupChart);
  fs.writeFileSync(path.join(outputDir, 'benchmark-results.json'), `${JSON.stringify(results, null, 2)}\n`);
  fs.writeFileSync(path.join(outputDir, 'BENCHMARK.md'), report);

  await warmupPool.destroy();
  console.log(`\nWrote results to ${path.relative(process.cwd(), outputDir)}`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
