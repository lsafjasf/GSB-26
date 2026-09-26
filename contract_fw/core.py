"""框架核心：声明式契约用例的执行、观测、比较与兼容性判定。

契约用例为 dict，字段说明：
    id           用例标识（必填，唯一）
    description  描述
    category     分类（正常/边界/非法参数/缺失字段/重复调用/超时/部分失败/副作用...）
    severity     严重度：minor | major | critical（用于兼容性判定）
    op           调用实现的操作名
    input        调用入参（dict）
    expect       {"result": 值} 或 {"error": "异常类型名" 或 [类型名, ...]}
    timeout_ms   单次调用超时（毫秒），超时记为失败
    repeat       重复调用次数（>1 时要求各次行为一致，即幂等性检查）
    side_effects 期望的副作用名列表（实现通过 Context.emit 记录）

被测实现协议：对象需有 .name 属性和 .call(op, payload, ctx) 方法。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

FLOAT_TOL = 1e-9
SEVERITY_ORDER = {"minor": 1, "major": 2, "critical": 3}
DEFAULT_TIMEOUT_MS = 1000

VERDICT_REPLACEABLE = "可替换"
VERDICT_CONDITIONAL = "有条件替换"
VERDICT_INCOMPATIBLE = "不可替换"


class Context:
    """注入被测实现的上下文，用于记录副作用。"""

    def __init__(self):
        self.effects = []

    def emit(self, name, detail=None):
        self.effects.append({"name": name, "detail": detail})


def values_equal(a, b, tol=FLOAT_TOL):
    """深度比较：浮点近似、int 精确、int/float 混合按 Python 精确语义。"""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) <= tol * max(1.0, abs(a), abs(b))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(values_equal(a[k], b[k], tol) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(values_equal(x, y, tol) for x, y in zip(a, b))
    return a == b


@dataclass
class Outcome:
    """单次调用的观测结果。"""

    kind: str                  # "result" | "error" | "timeout"
    value: object = None       # 返回值，或异常类型名
    effects: tuple = ()        # 排序后的副作用名
    duration_ms: float = 0.0

    def describe(self):
        if self.kind == "result":
            text = "返回 %r" % (self.value,)
        elif self.kind == "error":
            text = "抛出 %s" % self.value
        else:
            text = "超时未返回"
        if self.effects:
            text += "（副作用 %s）" % list(self.effects)
        return text

    def same_behavior(self, other):
        if self.kind != other.kind or self.effects != other.effects:
            return False
        if self.kind == "result":
            return values_equal(self.value, other.value)
        return self.value == other.value


def run_once(impl, case):
    """在守护线程中执行一次调用，支持超时判定与副作用采集。"""
    ctx = Context()
    box = {}

    def target():
        try:
            box["outcome"] = Outcome(
                "result", impl.call(case["op"], dict(case.get("input") or {}), ctx)
            )
        except Exception as exc:  # 框架只关心异常类型名
            box["outcome"] = Outcome("error", type(exc).__name__)

    timeout_ms = case.get("timeout_ms", DEFAULT_TIMEOUT_MS)
    thread = threading.Thread(target=target, daemon=True)
    start = time.monotonic()
    thread.start()
    thread.join(timeout_ms / 1000.0)
    duration_ms = (time.monotonic() - start) * 1000.0
    effects = tuple(sorted(e["name"] for e in ctx.effects))
    if thread.is_alive():
        return Outcome("timeout", None, effects, duration_ms)
    outcome = box["outcome"]
    outcome.effects = effects
    outcome.duration_ms = duration_ms
    return outcome


@dataclass
class CaseResult:
    case: dict
    impl_name: str
    outcomes: list             # 每次调用的 Outcome（repeat 次）
    failures: list             # 不满足契约断言的描述

    @property
    def ok(self):
        return not self.failures

    @property
    def observed(self):
        return self.outcomes[0]


def run_case(impl, case):
    repeats = max(1, int(case.get("repeat", 1)))
    outcomes = [run_once(impl, case) for _ in range(repeats)]
    failures = []
    first = outcomes[0]
    expect = case.get("expect", {})
    timeout_ms = case.get("timeout_ms", DEFAULT_TIMEOUT_MS)

    if first.kind == "timeout":
        failures.append("调用超时（>%dms 未返回）" % timeout_ms)
    elif "error" in expect:
        want = expect["error"]
        want = list(want) if isinstance(want, (list, tuple)) else [want]
        if first.kind != "error":
            failures.append("期望抛出 %s，实际%s" % ("/".join(want), first.describe()))
        elif first.value not in want:
            failures.append("期望错误类型 %s，实际抛出 %s" % ("/".join(want), first.value))
    else:
        if first.kind != "result":
            failures.append("期望返回 %r，实际%s" % (expect.get("result"), first.describe()))
        elif not values_equal(first.value, expect.get("result")):
            failures.append("期望返回 %r，实际返回 %r" % (expect.get("result"), first.value))

    if "side_effects" in case:
        want_effects = tuple(sorted(case["side_effects"]))
        if first.effects != want_effects:
            failures.append(
                "期望副作用 %s，实际 %s" % (list(want_effects), list(first.effects))
            )

    for index, other in enumerate(outcomes[1:], start=2):
        if not first.same_behavior(other):
            failures.append(
                "重复调用不一致：第 1 次%s，第 %d 次%s"
                % (first.describe(), index, other.describe())
            )

    return CaseResult(case=case, impl_name=impl.name, outcomes=outcomes, failures=failures)


class Runner:
    """对单个实现顺序执行全部契约用例。"""

    def __init__(self, cases):
        ids = [c["id"] for c in cases]
        if len(ids) != len(set(ids)):
            raise ValueError("契约用例 id 存在重复")
        self.cases = cases

    def run(self, impl):
        return [run_case(impl, case) for case in self.cases]


@dataclass
class Diff:
    """候选实现相对参考实现的一处行为差异。"""

    case: dict
    reference: CaseResult
    candidate: CaseResult


def _behaviors_match(ref_result, cand_result):
    if len(ref_result.outcomes) != len(cand_result.outcomes):
        return False
    return all(
        a.same_behavior(b) for a, b in zip(ref_result.outcomes, cand_result.outcomes)
    )


def behavior_diffs(reference_results, candidate_results):
    """按用例逐一比较两个实现的观测行为（含重复调用的各次结果）。"""
    diffs = []
    for ref, cand in zip(reference_results, candidate_results):
        if not _behaviors_match(ref, cand):
            diffs.append(Diff(case=ref.case, reference=ref, candidate=cand))
    return diffs


def judge(diffs):
    """兼容性判定：无差异=可替换；仅 minor=有条件替换；否则不可替换。"""
    if not diffs:
        return VERDICT_REPLACEABLE, "全部契约点行为一致，无差异。"
    counts = {"minor": 0, "major": 0, "critical": 0}
    for d in diffs:
        counts[d.case.get("severity", "major")] += 1
    worst = max(SEVERITY_ORDER[s] for s, n in counts.items() if n)
    detail = "差异分布：critical=%d, major=%d, minor=%d。" % (
        counts["critical"], counts["major"], counts["minor"])
    if worst <= SEVERITY_ORDER["minor"]:
        return (VERDICT_CONDITIONAL,
                detail + "仅存在 minor 级差异，不影响核心语义，可在接受这些差异的前提下替换。")
    return (VERDICT_INCOMPATIBLE,
            detail + "存在 major/critical 级差异，核心行为或异常语义不一致，不可直接替换。")
