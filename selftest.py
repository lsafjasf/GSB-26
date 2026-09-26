#!/usr/bin/env python3
"""框架自测：用两个（实际三个）故意不一致的实现验证框架本身。

验证点：
1. 参考实现 Good 满足全部用例（框架无误报）；
2. Bad 相对 Good 的全部已知差异都被指出，且没有多余差异（无漏报、无误报）；
3. 兼容性判定正确：Good=可替换，Minor=有条件替换，Bad=不可替换；
4. 对照用例（两者行为一致）不产生任何失败或差异。

用法：python3 selftest.py；退出码 0 = 自测通过。
"""
import sys
import time

from contract_fw.core import (
    Runner,
    behavior_diffs,
    judge,
    VERDICT_REPLACEABLE,
    VERDICT_CONDITIONAL,
    VERDICT_INCOMPATIBLE,
)

CASES = [
    # 对照组：两个实现行为一致，框架不得报告任何差异
    {"id": "c_ok_result", "severity": "critical", "op": "add",
     "input": {"a": 1, "b": 2}, "expect": {"result": 3}},
    {"id": "c_ok_error", "severity": "critical", "op": "boom",
     "input": {}, "expect": {"error": "ValueError"}},
    # 差异组：Bad 与 Good 在以下各维度故意不一致
    {"id": "c_result", "severity": "major", "op": "mul",
     "input": {"a": 2, "b": 3}, "expect": {"result": 6}},
    {"id": "c_err_type", "severity": "critical", "op": "parse",
     "input": {}, "expect": {"error": "KeyError"}},
    {"id": "c_timeout", "severity": "major", "op": "slow",
     "input": {}, "timeout_ms": 100, "expect": {"result": 1}},
    {"id": "c_side_effect", "severity": "minor", "op": "emit",
     "input": {}, "expect": {"result": 0}, "side_effects": ["audit"]},
    {"id": "c_repeat", "severity": "major", "op": "counter",
     "input": {}, "repeat": 2, "expect": {"result": 5}},
    {"id": "c_partial", "severity": "critical", "op": "batch",
     "input": {"items": [1, 0, 2]},
     "expect": {"result": {"ok": [1, 2], "bad": [0]}}},
    # 仅 Minor 实现不一致的用例（minor 级）
    {"id": "c_minor_only", "severity": "minor", "op": "label",
     "input": {}, "expect": {"result": "v1"}},
]

EXPECTED_BAD_DIFFS = {
    "c_result", "c_err_type", "c_timeout", "c_side_effect", "c_repeat", "c_partial",
}
CONTROL_CASES = {"c_ok_result", "c_ok_error"}


class Good:
    """参考实现：满足全部用例。"""
    name = "good"

    def call(self, op, payload, ctx):
        if op == "add":
            return payload["a"] + payload["b"]
        if op == "boom":
            raise ValueError("boom")
        if op == "mul":
            return payload["a"] * payload["b"]
        if op == "parse":
            raise KeyError("missing")
        if op == "slow":
            return 1
        if op == "emit":
            ctx.emit("audit")
            return 0
        if op == "counter":
            return 5
        if op == "batch":
            items = payload["items"]
            return {"ok": [x for x in items if x], "bad": [x for x in items if not x]}
        if op == "label":
            return "v1"
        raise ValueError("unknown op")


class Bad:
    """故意不一致的实现：每个差异维度各埋一处。"""
    name = "bad"

    def __init__(self):
        self._counter_calls = 0

    def call(self, op, payload, ctx):
        if op == "add":
            return payload["a"] + payload["b"]
        if op == "boom":
            raise ValueError("boom")
        if op == "mul":
            return payload["a"] * payload["b"] + 1          # 结果不一致
        if op == "parse":
            raise ValueError("missing")                      # 错误类型不一致
        if op == "slow":
            time.sleep(0.5)                                  # 超时
            return 1
        if op == "emit":
            return 0                                         # 缺少副作用
        if op == "counter":
            self._counter_calls += 1
            return 5 if self._counter_calls == 1 else 6      # 重复调用不一致
        if op == "batch":
            raise ZeroDivisionError("bad item")              # 部分失败语义不一致
        if op == "label":
            return "v1"
        raise ValueError("unknown op")


class MinorOnly:
    """仅在 minor 级用例上与参考实现不一致。"""
    name = "minor-only"

    def __init__(self):
        self._good = Good()

    def call(self, op, payload, ctx):
        if op == "label":
            return "v2"  # minor 级差异
        return self._good.call(op, payload, ctx)


def check(condition, message, failures):
    status = "PASS" if condition else "FAIL"
    print("  [%s] %s" % (status, message))
    if not condition:
        failures.append(message)


def main():
    runner = Runner(CASES)
    good, bad, minor = Good(), Bad(), MinorOnly()
    res_good = runner.run(good)
    res_bad = runner.run(bad)
    res_minor = runner.run(minor)

    failures = []
    print("自测 1：参考实现满足全部用例（无误报）")
    check(all(r.ok for r in res_good),
          "Good 通过全部 %d 个用例" % len(CASES), failures)

    print("自测 2：Bad 的差异全部被指出且无漏报/误报")
    diffs_bad = behavior_diffs(res_good, res_bad)
    diff_ids = {d.case["id"] for d in diffs_bad}
    check(diff_ids == EXPECTED_BAD_DIFFS,
          "差异集合 == 预期 %s（实际 %s）" % (sorted(EXPECTED_BAD_DIFFS), sorted(diff_ids)),
          failures)
    bad_failed = {r.case["id"] for r in res_bad if not r.ok}
    check(bad_failed == EXPECTED_BAD_DIFFS,
          "Bad 未通过用例集合 == 预期（实际 %s）" % sorted(bad_failed), failures)
    check(all(r.ok for r in res_bad if r.case["id"] in CONTROL_CASES),
          "对照用例在 Bad 上也通过（无误报）", failures)

    print("自测 3：兼容性判定")
    check(judge(behavior_diffs(res_good, res_good))[0] == VERDICT_REPLACEABLE,
          "Good vs Good => 可替换", failures)
    check(judge(behavior_diffs(res_good, res_minor))[0] == VERDICT_CONDITIONAL,
          "MinorOnly vs Good => 有条件替换", failures)
    check(judge(diffs_bad)[0] == VERDICT_INCOMPATIBLE,
          "Bad vs Good => 不可替换", failures)

    print("自测 4：MinorOnly 仅在 minor 用例上有差异")
    diffs_minor = behavior_diffs(res_good, res_minor)
    check({d.case["id"] for d in diffs_minor} == {"c_minor_only"},
          "差异集合 == {c_minor_only}", failures)

    if failures:
        print("\n自测失败 %d 项：" % len(failures))
        for f in failures:
            print("  - %s" % f)
        return 1
    print("\nSELF-TEST PASSED：全部差异被指出，且无误报。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
