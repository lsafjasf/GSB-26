"""接口契约测试框架（仅标准库）。

核心概念：
- Op           : 一次对接口的调用（方法名 + 参数）。
- Expect       : 对一次调用结果的期望（返回值 或 异常类型名）。
- ContractCase : 声明式契约用例（输入、期望输出/错误、超时、副作用、重复调用、严重程度）。
- CaseResult   : 单个用例在单个实现上的执行结果（通过 / 失败及差异明细）。

执行器对每个实现逐一执行全部用例，每个用例使用工厂函数创建全新实例，
保证用例之间互不影响。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

MISSING = object()  # 哨兵：表示“不关心具体返回值”

SEVERITY_RANK = {"minor": 1, "major": 2, "critical": 3}

VERDICT_REPLACEABLE = "可替换"
VERDICT_CONDITIONAL = "有条件替换"
VERDICT_INCOMPATIBLE = "不可替换"


@dataclass
class Op:
    """一次接口调用。"""

    method: str
    args: tuple = ()
    kwargs: Optional[dict] = None

    def __post_init__(self):
        if self.kwargs is None:
            self.kwargs = {}

    def describe(self) -> str:
        parts = [repr(a) for a in self.args]
        parts += [f"{k}={v!r}" for k, v in self.kwargs.items()]
        return f"{self.method}({', '.join(parts)})"


@dataclass
class Expect:
    """期望：要么返回 result，要么抛出名为 error 的异常。二者必居其一。"""

    result: Any = MISSING
    error: Optional[str] = None

    def describe(self) -> str:
        if self.error is not None:
            return f"抛出 {self.error}"
        if self.result is MISSING:
            return "任意返回值"
        return f"返回 {self.result!r}"


@dataclass
class ContractCase:
    """声明式契约用例。

    - setup        : 前置操作序列（任一失败则本用例判失败）。
    - call         : 被测调用。
    - expect       : 被测调用的期望。
    - repeat       : 被测调用重复执行次数（每次都必须满足 expect，用于重复调用/幂等）。
    - side_effects : 调用后的状态校验，(Op, Expect) 列表（用于副作用/部分失败检测）。
    - timeout_ms   : 单次调用超时（毫秒）。
    - point        : 契约点标识（用于覆盖清单）。
    - severity     : 不一致严重程度 minor / major / critical（用于兼容性判定）。
    """

    id: str
    point: str
    severity: str
    call: Op
    expect: Expect
    setup: List[Op] = field(default_factory=list)
    side_effects: List[Tuple[Op, Expect]] = field(default_factory=list)
    repeat: int = 1
    timeout_ms: int = 1000
    note: str = ""

    def __post_init__(self):
        if self.severity not in SEVERITY_RANK:
            raise ValueError(f"非法 severity: {self.severity!r}")
        if self.repeat < 1:
            raise ValueError("repeat 必须 >= 1")


@dataclass
class CaseResult:
    case_id: str
    impl: str
    status: str  # "pass" | "fail"
    failures: List[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.status == "pass"


def _invoke(impl: Any, op: Op) -> Tuple[str, Any]:
    """同步调用，返回 ("result", 值) 或 ("error", 异常类型名)。"""
    try:
        return ("result", getattr(impl, op.method)(*op.args, **op.kwargs))
    except Exception as exc:  # 框架只关心异常类型名，跨实现可比
        return ("error", type(exc).__name__)


def _invoke_with_timeout(impl: Any, op: Op, timeout_ms: int) -> Tuple[str, Any]:
    """带超时的调用，超时返回 ("timeout", 描述)。"""
    box: Dict[str, Any] = {}

    def target():
        box["outcome"] = _invoke(impl, op)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout_ms / 1000.0)
    if thread.is_alive():
        return ("timeout", f"超过 {timeout_ms}ms 未返回")
    return box["outcome"]


def _check(outcome: Tuple[str, Any], expect: Expect) -> Optional[str]:
    """比对实际结果与期望，不一致时返回差异描述，一致返回 None。"""
    kind, value = outcome
    if kind == "timeout":
        return f"期望{expect.describe()}，实际超时（{value}）"
    if expect.error is not None:
        if kind == "error" and value == expect.error:
            return None
        actual = f"抛出 {value}" if kind == "error" else f"返回 {value!r}"
        return f"期望{expect.describe()}，实际{actual}"
    if expect.result is MISSING:
        if kind == "result":
            return None
        return f"期望正常返回，实际抛出 {value}"
    if kind == "result" and value == expect.result:
        return None
    actual = f"返回 {value!r}" if kind == "result" else f"抛出 {value}"
    return f"期望{expect.describe()}，实际{actual}"


def run_case(factory: Callable[[], Any], impl_name: str, case: ContractCase) -> CaseResult:
    """在单个实现的新实例上执行一个契约用例。"""
    impl = factory()
    failures: List[str] = []

    for op in case.setup:
        outcome = _invoke_with_timeout(impl, op, case.timeout_ms)
        if outcome[0] != "result":
            failures.append(f"前置操作 {op.describe()} 失败: {outcome[0]} {outcome[1]!r}")
            return CaseResult(case.id, impl_name, "fail", failures)

    for i in range(case.repeat):
        outcome = _invoke_with_timeout(impl, case.call, case.timeout_ms)
        diff = _check(outcome, case.expect)
        if diff is not None:
            label = case.call.describe() if case.repeat == 1 else f"{case.call.describe()} 第{i + 1}次"
            failures.append(f"{label}: {diff}")
        if outcome[0] == "timeout":
            break  # 超时后实例状态不可信，停止重复

    for op, expect in case.side_effects:
        outcome = _invoke_with_timeout(impl, op, case.timeout_ms)
        diff = _check(outcome, expect)
        if diff is not None:
            failures.append(f"副作用校验 {op.describe()}: {diff}")

    return CaseResult(case.id, impl_name, "fail" if failures else "pass", failures)


def run_contract(factory: Callable[[], Any], impl_name: str,
                 cases: List[ContractCase]) -> List[CaseResult]:
    """对一个实现执行全部契约用例。"""
    return [run_case(factory, impl_name, case) for case in cases]


@dataclass
class Verdict:
    level: str  # 可替换 / 有条件替换 / 不可替换
    reason: str
    failures_by_severity: Dict[str, List[str]]  # severity -> [case_id]


def judge(results: List[CaseResult], cases: List[ContractCase]) -> Verdict:
    """兼容性判定。

    - 无失败                       -> 可替换
    - 失败最高严重程度为 minor     -> 有条件替换
    - 存在 major / critical 失败   -> 不可替换
    """
    cases_by_id = {c.id: c for c in cases}
    by_sev: Dict[str, List[str]] = {"critical": [], "major": [], "minor": []}
    for r in results:
        if not r.passed:
            by_sev[cases_by_id[r.case_id].severity].append(r.case_id)

    total = sum(len(v) for v in by_sev.values())
    if total == 0:
        return Verdict(VERDICT_REPLACEABLE, "全部契约用例通过，行为一致。", by_sev)
    if by_sev["critical"] or by_sev["major"]:
        parts = []
        for sev in ("critical", "major", "minor"):
            if by_sev[sev]:
                parts.append(f"{sev}x{len(by_sev[sev])}")
        reason = (f"存在核心行为不一致（{', '.join(parts)}），"
                  f"直接替换会改变调用方可观察行为。")
        return Verdict(VERDICT_INCOMPATIBLE, reason, by_sev)
    reason = (f"仅存在 {len(by_sev['minor'])} 个轻微(minor)不一致："
              f"{', '.join(by_sev['minor'])}；不触及核心语义，"
              f"调用方若不依赖这些边界行为即可替换。")
    return Verdict(VERDICT_CONDITIONAL, reason, by_sev)
