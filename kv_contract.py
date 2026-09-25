"""KVStore 接口的声明式契约用例。

每个用例声明：输入（setup + call）、期望输出或期望错误类型、
超时、副作用校验、重复调用次数、所属契约点与严重程度。
"""

from contractfw import ContractCase, Expect, Op

# 契约要求覆盖的全部契约点（用于检出“声明了要求但无用例”的未覆盖项）
REQUIRED_POINTS = [
    "core.read-write",
    "core.overwrite",
    "core.batch",
    "core.size",
    "error.missing-key",
    "error.invalid-key",
    "error.invalid-value",
    "boundary.key-format",
    "idempotency.repeat-call",
    "atomicity.partial-failure",
    "performance.timeout",
    "concurrency.thread-safety",  # 故意不提供用例：演示“未覆盖”检出
]

CASES = [
    # ---------- 核心行为 ----------
    ContractCase(
        id="core.get-after-set",
        point="core.read-write",
        severity="critical",
        setup=[Op("set", ("a", 1))],
        call=Op("get", ("a",)),
        expect=Expect(result=1),
        note="写入后可读回",
    ),
    ContractCase(
        id="core.set-overwrite",
        point="core.overwrite",
        severity="major",
        setup=[Op("set", ("k", "old"))],
        call=Op("set", ("k", "new")),
        expect=Expect(result=None),
        side_effects=[(Op("get", ("k",)), Expect(result="new"))],
        note="同键覆盖写，读到最新值",
    ),
    ContractCase(
        id="core.set-many-ok",
        point="core.batch",
        severity="major",
        call=Op("set_many", ({"x": 1, "y": 2},)),
        expect=Expect(result=2),
        side_effects=[
            (Op("size", ()), Expect(result=2)),
            (Op("get", ("x",)), Expect(result=1)),
        ],
    ),
    ContractCase(
        id="core.size-empty",
        point="core.size",
        severity="minor",
        call=Op("size", ()),
        expect=Expect(result=0),
    ),

    # ---------- 异常与非法参数 ----------
    ContractCase(
        id="error.get-missing",
        point="error.missing-key",
        severity="critical",
        call=Op("get", ("no-such-key",)),
        expect=Expect(error="KeyNotFoundError"),
        note="缺失字段/键必须抛契约规定的错误类型",
    ),
    ContractCase(
        id="error.set-empty-key",
        point="error.invalid-key",
        severity="major",
        call=Op("set", ("", 1)),
        expect=Expect(error="InvalidKeyError"),
    ),
    ContractCase(
        id="error.set-nonstring-key",
        point="error.invalid-key",
        severity="major",
        call=Op("set", (123, 1)),
        expect=Expect(error="InvalidKeyError"),
        note="错误类型必须一致，内置 TypeError 不算契约行为",
    ),
    ContractCase(
        id="error.delete-bad-key",
        point="error.invalid-key",
        severity="major",
        call=Op("delete", ("",)),
        expect=Expect(error="InvalidKeyError"),
    ),
    ContractCase(
        id="error.set-none-value",
        point="error.invalid-value",
        severity="major",
        call=Op("set", ("k", None)),
        expect=Expect(error="InvalidValueError"),
    ),

    # ---------- 边界 ----------
    ContractCase(
        id="boundary.whitespace-key",
        point="boundary.key-format",
        severity="minor",
        call=Op("set", ("   ", 1)),
        expect=Expect(error="InvalidKeyError"),
        note="纯空白键视为非法（minor：调用方较少依赖）",
    ),

    # ---------- 重复调用 / 幂等 ----------
    ContractCase(
        id="idempotency.set-repeat",
        point="idempotency.repeat-call",
        severity="major",
        call=Op("set", ("k", "v")),
        expect=Expect(result=None),
        repeat=3,
        side_effects=[(Op("size", ()), Expect(result=1))],
        note="重复写入同一键值，结果与状态都稳定",
    ),
    ContractCase(
        id="idempotency.delete-twice",
        point="idempotency.repeat-call",
        severity="major",
        setup=[Op("set", ("k", "v"))],
        call=Op("delete", ("k",)),
        expect=Expect(result=True),
        side_effects=[
            (Op("delete", ("k",)), Expect(result=False)),
            (Op("size", ()), Expect(result=0)),
        ],
        note="重复删除：第一次 True，之后 False",
    ),

    # ---------- 部分失败 / 原子性 ----------
    ContractCase(
        id="atomicity.set-many-partial-failure",
        point="atomicity.partial-failure",
        severity="critical",
        call=Op("set_many", ({"ok1": 1, "bad": None, "ok2": 2},)),
        expect=Expect(error="InvalidValueError"),
        side_effects=[
            (Op("size", ()), Expect(result=0)),
            (Op("get", ("ok1",)), Expect(error="KeyNotFoundError")),
        ],
        note="批量写中途失败不得留下部分写入",
    ),

    # ---------- 超时 ----------
    ContractCase(
        id="performance.size-timeout",
        point="performance.timeout",
        severity="major",
        call=Op("size", ()),
        expect=Expect(result=0),
        timeout_ms=100,
        note="size 必须在 100ms 内返回",
    ),
]
