# Benchmark results

- Environment: Node v18.19.1, AMD Ryzen 7 9700X 8-Core Processor
- Reported cores: 8; benchmark cap: 8 worker threads
- Shape: 640x640 * 640x640; repetitions per point: 5; statistic: median
- Best block size by parallel median time in this sweep: 64
- Naive i-k-j baseline: 161.792 ms

## Block-size curve

![Block-size curve](block-size.svg)

| Block | Serial ms | Parallel ms | Serial GFLOPS | Parallel GFLOPS |
|---:|---:|---:|---:|---:|
| 4 | 272.353 | 76.504 | 1.925 | 6.853 |
| 8 | 205.246 | 63.671 | 2.554 | 8.234 |
| 16 | 195.996 | 63.053 | 2.675 | 8.315 |
| 24 | 183.392 | 48.187 | 2.859 | 10.880 |
| 32 | 164.963 | 45.229 | 3.178 | 11.592 |
| 48 | 166.533 | 46.465 | 3.148 | 11.284 |
| 64 | 159.944 | 37.163 | 3.278 | 14.108 |
| 96 | 159.676 | 38.516 | 3.283 | 13.612 |
| 128 | 154.843 | 38.894 | 3.386 | 13.480 |
| 192 | 166.710 | 47.510 | 3.145 | 11.035 |
| 256 | 164.138 | 58.702 | 3.194 | 8.931 |
| 640 | 156.048 | 156.190 | 3.360 | 3.357 |

Small blocks have more block-loop overhead and may under-use each cache line. Very large blocks stop fitting comfortably in L1/L2, so reused B and C values are evicted more often; they also shrink the tile count to ceil(M/B)*ceil(N/B), which can leave most workers idle. On this Node/i-k-j workload the unblocked serial run is competitive, but the parallel optimum is near 64: a block needs to be cache-friendly while still producing many more tiles than workers. Use that range as a starting point and tune once on the target CPU.

## Thread scaling

![Speedup curve](speedup.svg)

| Workers | Parallel ms | Speedup | Efficiency |
|---:|---:|---:|---:|
| 1 | 158.798 | 1.007 | 100.722% |
| 2 | 81.289 | 1.968 | 98.379% |
| 4 | 44.173 | 3.621 | 90.521% |
| 8 | 40.251 | 3.974 | 49.671% |

Speedup is sublinear because the machine has shared memory bandwidth and cache capacity, worker startup/message synchronization has fixed overhead, OS scheduling and SMT threads do not add equal execution capacity, and work is divided by output tiles so irregular shapes or a small number of tiles can leave load imbalance. When the requested worker count exceeds usable tiles, extra workers are deliberately not launched.

## Memory bound

For an MxK times KxN product, the library stores one C and shares A/B by SharedArrayBuffer. The floating-point payload is 8(MK + KN + MN) bytes: the caller's A and B plus exactly one output C, with no per-block copies. At this benchmark size that is 9.38 MiB. Workers do not receive block arrays; each worker receives constant-size metadata (approximately 256 bytes), giving a measured-configuration upper bound near 9.38 MiB plus Node runtime/thread stacks.
