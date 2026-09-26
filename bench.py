"""基准对比：重构前后在典型程序上的耗时。

运行：python3 bench.py
"""

import time

import vm_legacy
import vm_refactored
from programs import FIB_PROGRAM, THROW_LOOP_PROGRAM

REPS = 3000


def bench(run_fn, program):
    # 预热
    for _ in range(50):
        run_fn(program)
    start = time.perf_counter()
    for _ in range(REPS):
        run_fn(program)
    return (time.perf_counter() - start) / REPS


def main():
    for name, program in (("fib(25) 循环", FIB_PROGRAM),
                          ("try/throw 循环 x200", THROW_LOOP_PROGRAM)):
        legacy_result = vm_legacy.run(program)
        refactored_result = vm_refactored.run(program)
        assert (legacy_result.status, legacy_result.output) == \
               (refactored_result.status, refactored_result.output), name
        legacy_ms = bench(vm_legacy.run, program) * 1000
        refactored_ms = bench(vm_refactored.run, program) * 1000
        ratio = refactored_ms / legacy_ms
        print("%-22s legacy=%7.3f ms  refactored=%7.3f ms  ratio=%.2fx"
              % (name, legacy_ms, refactored_ms, ratio))
        print("    output=%r steps=%d"
              % (legacy_result.output, legacy_result.steps))


if __name__ == "__main__":
    main()
