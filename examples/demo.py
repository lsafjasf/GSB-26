"""演示：对含有多处错误的源文件做容错解析，打印部分语法树与错误列表。

运行：python3 examples/demo.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from miniparser import parse

SOURCE = """\
let total = 0;
let rate = ;
if (total >= 0) {
  let bonus = total * 2
  print bonus;
}
print total;
let broken = 1 +;
"""

def show(node, indent=0):
    mark = "  <-- 恢复产物（不确定区域）" if node.recovered else ""
    detail = {k: v for k, v in node.props.items()
              if not hasattr(v, "props") and not isinstance(v, list)}
    print("  " * indent + f"{node.kind} @{node.pos} {detail}{mark}")
    for value in node.props.values():
        if hasattr(value, "props") and hasattr(value, "kind"):
            show(value, indent + 1)
        elif isinstance(value, list):
            for item in value:
                if hasattr(item, "kind"):
                    show(item, indent + 1)

result = parse(SOURCE)

print("=== 源文件 ===")
print(SOURCE)
print("=== 错误列表（按位置排序） ===")
for err in result.errors:
    print(f"  {err}")

print(f"\nok={result.ok}  incomplete={result.incomplete}")
print(f"不确定区域数量: {len(result.uncertain_regions())}")

print("\n=== 部分语法树 ===")
show(result.tree)
