#!/usr/bin/env python3
"""契约测试入口：对全部实现执行样例契约并输出报告。

用法：python3 run_contracts.py
退出码：0 = 全部实现满足契约断言；1 = 存在未满足契约断言的实现。
"""
import sys

from contract_fw.core import Runner
from contract_fw.report import full_report
from examples.cases import CASES
from examples.impl_v1 import ImplV1
from examples.impl_v2 import ImplV2
from examples.impl_stub import ImplStub


def main():
    impls = [ImplV1(), ImplV2(), ImplStub()]
    reference = impls[0].name
    runner = Runner(CASES)
    results_by_impl = {impl.name: runner.run(impl) for impl in impls}
    print(full_report(CASES, results_by_impl, reference))
    all_ok = all(r.ok for results in results_by_impl.values() for r in results)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
