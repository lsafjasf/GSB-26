"""Mutant execution, classification, parallelism and incremental caching."""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field

from .mutator import Mutant, apply_mutant, collect_mutants

KILLED = "killed"
SURVIVED = "survived"
TIMEOUT = "timeout"
INCOMPETENT = "incompetent"
STATUSES = (KILLED, SURVIVED, TIMEOUT, INCOMPETENT)


@dataclass
class MutantResult:
    mutant: Mutant
    status: str
    duration: float = 0.0
    from_cache: bool = False
    equivalent_reason: str | None = None

    @property
    def equivalent(self) -> bool:
        return self.equivalent_reason is not None


def _run_one_star(args):
    return _run_one(*args)


def _run_one(mutant, module_source, module_name, test_source, test_name,
             timeout):
    """Execute a single mutant in a subprocess. Module-level for pickling."""
    try:
        mutated = apply_mutant(module_source, mutant)
        compile(mutated, module_name + ".py", "exec")
    except SyntaxError:
        return mutant.id, INCOMPETENT, 0.0
    tmp = tempfile.mkdtemp(prefix="muttest_")
    with open(os.path.join(tmp, module_name + ".py"), "w") as fh:
        fh.write(mutated)
    with open(os.path.join(tmp, test_name + ".py"), "w") as fh:
        fh.write(test_source)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    start = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", test_name],
            cwd=tmp, capture_output=True, timeout=timeout, env=env)
        status = SURVIVED if proc.returncode == 0 else KILLED
    except subprocess.TimeoutExpired:
        status = TIMEOUT
    return mutant.id, status, time.monotonic() - start


class ResultCache:
    """Incremental execution: results are reused while neither the module
    source, the test source nor the mutant itself changes."""

    def __init__(self, path, fingerprint):
        self.path = path
        self.fingerprint = fingerprint
        self.data = {}
        if path and os.path.exists(path):
            try:
                with open(path) as fh:
                    self.data = json.load(fh)
            except (json.JSONDecodeError, OSError):
                self.data = {}

    def key(self, mutant: Mutant) -> str:
        raw = f"{self.fingerprint}|{mutant.id}|{mutant.detail}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, mutant: Mutant):
        return self.data.get(self.key(mutant))

    def put(self, mutant: Mutant, status: str):
        self.data[self.key(mutant)] = status

    def save(self):
        if not self.path:
            return
        with open(self.path, "w") as fh:
            json.dump(self.data, fh, indent=1, sort_keys=True)


def fingerprint(module_source: str, test_source: str) -> str:
    return hashlib.sha256((module_source + "\0" + test_source).encode()).hexdigest()


@dataclass
class RunSummary:
    results: list[MutantResult] = field(default_factory=list)
    executions: int = 0          # mutants actually executed in a subprocess
    cache_hits: int = 0          # mutants served from the incremental cache
    wall_time: float = 0.0
    jobs: int = 1
    timeout: float = 5.0

    def by_status(self, status: str) -> list[MutantResult]:
        return [r for r in self.results if r.status == status]

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def equivalents(self) -> list[MutantResult]:
        return [r for r in self.results if r.equivalent]

    def score(self) -> tuple[float, int, int]:
        """Mutation score. The denominator excludes mutants marked as
        equivalent and mutants that cannot compile/load. Timeouts count as
        killed (a hanging test suite is a detected defect)."""
        equivalent = len(self.equivalents)
        incompetent = len(self.by_status(INCOMPETENT))
        denominator = self.total - equivalent - incompetent
        killed = len(self.by_status(KILLED)) + len(self.by_status(TIMEOUT))
        killed -= sum(1 for r in self.equivalents
                      if r.status in (KILLED, TIMEOUT))
        score = (killed / denominator * 100.0) if denominator else 100.0
        return score, killed, denominator


def run_suite(module_path: str, test_path: str, *, timeout: float = 5.0,
              jobs: int = 1, cache_path: str | None = None,
              equivalents: dict | None = None) -> RunSummary:
    with open(module_path) as fh:
        module_source = fh.read()
    with open(test_path) as fh:
        test_source = fh.read()
    module_name = os.path.splitext(os.path.basename(module_path))[0]
    test_name = os.path.splitext(os.path.basename(test_path))[0]

    mutants = collect_mutants(module_source)
    equivalents = equivalents or {}
    cache = ResultCache(cache_path, fingerprint(module_source, test_source))

    summary = RunSummary(jobs=jobs, timeout=timeout)
    pending: list[Mutant] = []
    for mutant in mutants:
        result = MutantResult(mutant=mutant, status="",
                              equivalent_reason=equivalents.get(mutant.id))
        cached = cache.get(mutant)
        if cached is not None:
            result.status = cached
            result.from_cache = True
            summary.results.append(result)
            summary.cache_hits += 1
        else:
            summary.results.append(result)
            pending.append(mutant)

    start = time.monotonic()
    tasks = [(m, module_source, module_name, test_source, test_name, timeout)
             for m in pending]
    if jobs > 1 and len(tasks) > 1:
        with concurrent.futures.ProcessPoolExecutor(max_workers=jobs) as pool:
            outcomes = list(pool.map(_run_one_star, tasks))
    else:
        outcomes = [_run_one(*t) for t in tasks]
    summary.wall_time = time.monotonic() - start
    summary.executions = len(outcomes)

    by_id = {m.id: m for m in pending}
    for mutant_id, status, duration in outcomes:
        mutant = by_id[mutant_id]
        cache.put(mutant, status)
        for result in summary.results:
            if result.mutant is mutant:
                result.status = status
                result.duration = duration
                break
    cache.save()
    return summary
