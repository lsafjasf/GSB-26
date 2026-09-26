"""核心数据模型与重复执行引擎。

每个重复轮次（repetition）在独立子进程中执行整个测试集一遍，
保证轮次之间模块级状态完全隔离（这也是真实 flaky 场景的前提）。
"""
from __future__ import annotations

import importlib
import platform
import random
import time
import traceback
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone


@dataclass
class TestResult:
    test_id: str
    status: str          # "passed" | "failed" | "error"
    duration: float      # 秒
    error: str = ""


@dataclass
class RunRecord:
    """一次完整重复执行（整个测试集跑一遍）的记录。"""
    repetition: int
    mode: str            # "fixed" | "shuffled"
    seed: int
    jobs: int
    order: list          # 实际执行顺序（test_id 列表）
    started_at: str
    duration: float
    results: list        # list[TestResult]


def discover_tests(module_name):
    """按定义顺序发现模块中所有 test_* 函数。"""
    module = importlib.import_module(module_name)
    names = [name for name, fn in vars(module).items()
             if name.startswith("test_") and callable(fn)]
    return names


def _run_one_repetition(args):
    """在子进程中执行一轮。args 必须可 pickle。"""
    module_name, test_names, mode, seed, rep, jobs = args
    started = datetime.now(timezone.utc).isoformat()
    t0 = time.perf_counter()
    module = importlib.import_module(module_name)
    # worker 进程会被复用，必须 reload 以清除上一轮残留的模块级状态
    module = importlib.reload(module)

    order = list(test_names)
    if mode == "shuffled":
        random.Random(seed * 100003 + rep).shuffle(order)

    results = []
    for name in order:
        fn = getattr(module, name)
        start = time.perf_counter()
        status, error = "passed", ""
        try:
            fn()
        except AssertionError as exc:
            status = "failed"
            error = f"AssertionError: {exc}"[:500]
        except Exception:
            status = "error"
            error = traceback.format_exc()[-500:]
        results.append(TestResult(
            test_id=f"{module_name}.{name}",
            status=status,
            duration=time.perf_counter() - start,
            error=error,
        ))
    return RunRecord(
        repetition=rep, mode=mode, seed=seed, jobs=jobs,
        order=[f"{module_name}.{n}" for n in order],
        started_at=started, duration=time.perf_counter() - t0,
        results=results,
    )


def run_suite(module_name, repeats=20, mode="both", jobs=1, seed=42):
    """重复执行测试集，返回可 JSON 序列化的结果字典。

    mode: "fixed"（定义顺序）| "shuffled"（每轮按种子打乱）| "both"
    每个重复轮次都在独立子进程中运行，保证环境隔离。
    """
    test_names = discover_tests(module_name)
    modes = ["fixed", "shuffled"] if mode == "both" else [mode]

    tasks = []
    for m in modes:
        for rep in range(repeats):
            tasks.append((module_name, test_names, m, seed, rep, jobs))

    records = []
    with ProcessPoolExecutor(max_workers=max(1, jobs)) as pool:
        for rec in pool.map(_run_one_repetition, tasks):
            records.append(rec)

    return {
        "module": module_name,
        "repeats_per_mode": repeats,
        "modes": modes,
        "env": {
            "seed": seed,
            "jobs": jobs,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "cpu_count": __import__("os").cpu_count(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        "records": [asdict(r) for r in records],
    }
