"""核心执行引擎：测试发现、单轮执行、结果记录。

记录内容（每次测试执行一条记录）：
  - 结果（pass/fail/error）与耗时
  - 环境信息：随机种子、并行度、执行顺序、Python/平台、时间戳
"""
from __future__ import annotations

import importlib.util
import json
import os
import platform
import random
import sys
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Callable


@dataclass
class RunRecord:
    run_id: str          # 一批重复执行的唯一 ID
    round_index: int     # 第几轮（0 起）
    mode: str            # "fixed"（固定顺序）| "shuffled"（打乱顺序）
    test_id: str         # 测试名
    status: str          # "pass" | "fail" | "error"
    duration: float      # 秒
    seed: int            # 本轮随机种子
    jobs: int            # 并行度
    order_index: int     # 该测试在本轮执行顺序中的位置
    order: list          # 本轮完整执行顺序
    timestamp: float
    python: str
    platform: str
    error: str = ""


def load_tests(path: str) -> dict[str, Callable[[], None]]:
    """从 Python 文件中加载所有 test_* 函数（无参数、无返回值，抛异常即失败）。"""
    name = "flakyhunter_target_" + uuid.uuid4().hex[:8]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    tests = {
        attr: getattr(module, attr)
        for attr in dir(module)
        if attr.startswith("test_") and callable(getattr(module, attr))
    }
    if not tests:
        raise ValueError(f"{path} 中没有发现 test_* 函数")
    return tests


def _run_one(test_id: str, fn: Callable[[], None]) -> tuple[str, float, str]:
    start = time.perf_counter()
    try:
        fn()
        return "pass", time.perf_counter() - start, ""
    except AssertionError:
        return "fail", time.perf_counter() - start, traceback.format_exc(limit=3)
    except Exception:
        return "error", time.perf_counter() - start, traceback.format_exc(limit=3)


def execute_round(
    tests: dict[str, Callable[[], None]],
    *,
    run_id: str,
    round_index: int,
    mode: str,
    seed: int,
    jobs: int,
) -> list[RunRecord]:
    """执行一轮完整测试集。seed 同时控制顺序打乱与测试内部随机性（经 FLAKY_SEED 暴露）。"""
    order = sorted(tests)  # 固定顺序：按名字排序，保证可复现
    if mode == "shuffled":
        rng = random.Random(seed)
        rng.shuffle(order)
    os.environ["FLAKY_SEED"] = str(seed)

    results: dict[str, tuple[str, float, str]] = {}
    if jobs > 1:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = {tid: pool.submit(_run_one, tid, tests[tid]) for tid in order}
            for tid in order:
                results[tid] = futures[tid].result()
    else:
        for tid in order:
            results[tid] = _run_one(tid, tests[tid])

    records = []
    for idx, tid in enumerate(order):
        status, duration, error = results[tid]
        records.append(
            RunRecord(
                run_id=run_id,
                round_index=round_index,
                mode=mode,
                test_id=tid,
                status=status,
                duration=duration,
                seed=seed,
                jobs=jobs,
                order_index=idx,
                order=list(order),
                timestamp=time.time(),
                python=sys.version.split()[0],
                platform=platform.platform(),
                error=error,
            )
        )
    return records


class JsonlSink:
    """把记录追加写入 JSONL 文件。"""

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    def write(self, records: list[RunRecord]) -> None:
        with open(self.path, "a", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")


def load_records(path: str) -> list[dict]:
    records = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records
