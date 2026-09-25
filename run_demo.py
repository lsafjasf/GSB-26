"""演示入口：对三个实现执行契约，输出控制台摘要并生成 REPORT.md。"""

from contractfw import run_contract
from kv_contract import CASES, REQUIRED_POINTS
from report import render_console, render_markdown
from impls.good_impl import GoodKV
from impls.bad_impl import BadKV
from impls.minor_impl import MinorKV

IMPLS = [
    ("good(参考实现)", GoodKV),
    ("bad(问题替身)", BadKV),
    ("minor(近似实现)", MinorKV),
]


def main():
    impl_results = {
        name: run_contract(factory, name, CASES) for name, factory in IMPLS
    }
    print(render_console(CASES, impl_results))
    md, _ = render_markdown(
        "KVStore 接口契约报告", CASES, impl_results, REQUIRED_POINTS)
    with open("REPORT.md", "w", encoding="utf-8") as f:
        f.write(md)
    print("\n完整报告已写入 REPORT.md")


if __name__ == "__main__":
    main()
